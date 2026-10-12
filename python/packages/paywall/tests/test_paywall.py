import pytest
from paywall_support import SECRET, FakeDns, MemCache, browser
from sqlalchemy import text
from tsp.paywall import (
    InsufficientCredits,
    Item,
    Paywall,
    paywall_jsonld,
    register_with_compliance,
    sign,
    verify,
)

ORG = "pub1"


def item(n):
    return Item(f"recipe-{n}", ORG, "recipe")


async def read(pw, db, visitor, it):
    async with db.session() as s:
        d = await pw.check(s, visitor, it)
        await pw.record_read(s, visitor, it, d)
    return d


async def one_free_per_week(pw, db, **kw):
    async with db.session() as s:
        await pw.set_policy(s, ORG, "weekly", free_items=1, window_days=7, **kw)


# -- the shared-network case comes first: it is the one that must not go wrong -----------------


async def test_many_readers_behind_one_ip_are_not_walled(pw, db):
    await one_free_per_week(pw, db)
    walled = 0
    for n in range(50):  # same network, same browser family, same language, every one a new person
        d = await read(pw, db, browser(), item(n % 7))
        walled += not d.allowed
    # a few early arrivals can be mistaken for one person coming back without a cookie; once the
    # network is recognised as shared (more than 3 meters in a day) nobody else is
    assert walled <= pw.shared_after - 1
    late = [await read(pw, db, browser(), item(99)) for _ in range(5)]
    assert all(d.allowed for d in late)


# -- the basic meter ------------------------------------------------------------------------------


async def test_hard_wall_one_item_per_week(pw, db):
    await one_free_per_week(pw, db)
    first = await read(pw, db, browser(), item(1))
    assert (
        first.allowed and first.reason == "free_quota" and first.remaining == 0 and first.set_cookie
    )
    c = first.set_cookie
    again = await read(pw, db, browser(cookie=c), item(1))  # re-reading the same item is free
    assert again.allowed and again.set_cookie is None
    second = await read(pw, db, browser(cookie=c), item(2))
    assert not second.allowed and second.reason == "wall" and second.wall == "hard"
    # the window moves on
    async with db.session() as s:
        await s.execute(text("update pw_reads set at = now() - interval '8 days'"))
    assert (await read(pw, db, browser(cookie=c), item(2))).allowed


async def test_clearing_the_cookie_does_not_reset_the_meter(pw, db):
    await one_free_per_week(pw, db)
    await read(pw, db, browser(), item(1))
    d = await read(pw, db, browser(), item(2))  # no cookie sent: same device signal
    assert not d.allowed
    # and the new cookie keeps the inherited count on the next request
    d2 = await read(pw, db, browser(cookie=d.set_cookie), item(3))
    assert not d2.allowed


async def test_a_different_device_has_its_own_meter(pw, db):
    await one_free_per_week(pw, db)
    await read(pw, db, browser(), item(1))
    other = await read(
        pw, db, browser(ip="198.51.100.9", ua="Mozilla/5.0 Chrome/129", lang="hi-IN"), item(2)
    )
    assert other.allowed


async def test_tampered_or_foreign_cookies_are_ignored(pw, db):
    await one_free_per_week(pw, db)
    good = (await read(pw, db, browser(), item(1))).set_cookie
    forged = good[:-4] + "0000"
    assert (
        verify(SECRET, forged) is None
        and verify("other-secret-value-0123456789abcdef", good) is None
    )
    assert (
        verify(SECRET, good) is not None and verify(SECRET, sign(SECRET, "m", issued=1)) is None
    )  # expired
    d = await read(pw, db, browser(ip="198.51.100.1", cookie=forged), item(2))
    assert d.set_cookie and d.set_cookie != forged  # a fresh meter was issued


async def test_policies_apply_by_item_type_and_unmetered_content_is_open(pw, db):
    await one_free_per_week(pw, db, applies_to=["recipe"])
    assert (await read(pw, db, browser(), Item("about", ORG, "page"))).reason == "open"
    assert (await read(pw, db, browser(), Item("x", "other-org", "recipe"))).reason == "open"


async def test_require_account_after_n_anonymous_reads(pw, db):
    async with db.session() as s:
        await pw.set_policy(s, ORG, "w", free_items=5, window_days=7, require_account_after=2)
    c = (await read(pw, db, browser(), item(1))).set_cookie
    assert (await read(pw, db, browser(cookie=c), item(2))).allowed
    d = await read(pw, db, browser(cookie=c), item(3))
    assert not d.allowed and d.require_account
    signed_in = await read(pw, db, browser(cookie=c, user="u1"), item(3))
    assert signed_in.allowed  # a free account moves the meter to the user (3 of 5 used)
    assert signed_in.remaining == 5 - 2 - 1


# -- who gets in regardless ---------------------------------------------------------------------


async def test_subscribers_always_pass_and_are_not_counted(db):
    async def entitled(s, user, org):
        return user == "sub1"

    pw = Paywall(SECRET, MemCache(), entitled=entitled, resolver=FakeDns({}))
    await one_free_per_week(pw, db)
    for n in range(5):
        d = await read(pw, db, browser(user="sub1"), item(n))
        assert d.allowed and d.reason == "entitled"
    async with db.session() as s:
        assert (await s.execute(text("select count(*) from pw_reads"))).scalar_one() == 0


async def test_only_verified_crawlers_get_the_full_text(db):
    googlebot = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    dns = FakeDns(
        {
            "66.249.66.1": "crawl-66-249-66-1.googlebot.com",
            "crawl-66-249-66-1.googlebot.com": ["66.249.66.1"],
            "6.6.6.6": "evil.example.net",
            "7.7.7.7": "crawl-7.googlebot.com",
            "crawl-7.googlebot.com": ["8.8.8.8"],
        }
    )
    pw = Paywall(SECRET, MemCache(), resolver=dns)
    await one_free_per_week(pw, db)
    await read(pw, db, browser(), item(1))
    real = await read(pw, db, browser(ip="66.249.66.1", ua=googlebot), item(2))
    assert real.allowed and real.reason == "crawler"
    for ip in (
        "6.6.6.6",
        "7.7.7.7",
        "9.9.9.9",
    ):  # wrong host, forward lookup mismatch, no DNS at all
        fake = await read(pw, db, browser(ip=ip, ua=googlebot), item(1))
        second = await read(pw, db, browser(ip=ip, ua=googlebot, cookie=fake.set_cookie), item(2))
        assert fake.reason != "crawler" and not second.allowed


def test_paywall_markup_for_search_engines():
    j = paywall_jsonld("Fluffy pancakes", ".recipe-body")
    assert j["isAccessibleForFree"] is False and j["hasPart"]["cssSelector"] == ".recipe-body"


# -- gifts and credits ----------------------------------------------------------------------------


async def test_gift_links_open_one_item_up_to_max_uses(pw, db):
    await one_free_per_week(pw, db)
    async with db.session() as s:
        token = await pw.create_gift(s, "giver", item(5), max_uses=2, days=7)
        stored = (await s.execute(text("select token_hash from pw_gifts"))).scalar_one()
    assert token not in stored  # only a hash is kept
    results, readers = [], []
    for ua in (
        "Firefox/130",
        "Chrome/129",
        "Safari/17",
    ):  # three different devices, each out of free reads
        v = browser(ua=f"Mozilla/5.0 {ua}")
        c = (await read(pw, db, v, item(1))).set_cookie
        readers.append(c)
        d = await read(pw, db, browser(ua=f"Mozilla/5.0 {ua}", cookie=c, gift=token), item(5))
        results.append(d.reason)
    assert results == ["gift", "gift", "wall"]
    # the first reader comes back to the gifted item without using another use or the link
    back = await read(pw, db, browser(ua="Mozilla/5.0 Firefox/130", cookie=readers[0]), item(5))
    assert back.allowed and back.reason == "unlocked"
    wrong_item = await read(
        pw, db, browser(ip="198.51.100.30", ua="Mozilla/5.0 Opr/1", gift=token), item(6)
    )
    assert wrong_item.reason != "gift"


async def test_expired_gift_does_not_open(pw, db):
    await one_free_per_week(pw, db)
    async with db.session() as s:
        token = await pw.create_gift(s, "giver", item(5), days=7)
        await s.execute(text("update pw_gifts set expires_at = now() - interval '1 minute'"))
    c = (await read(pw, db, browser(), item(1))).set_cookie
    assert (await read(pw, db, browser(cookie=c, gift=token), item(5))).reason == "wall"


async def test_gift_creation_is_rate_limited(pw, db):
    async with db.session() as s:
        for _ in range(3):
            await pw.create_gift(s, "giver", item(1), limit_per_day=3)
        with pytest.raises(ValueError):
            await pw.create_gift(s, "giver", item(1), limit_per_day=3)


async def test_credits_unlock_items_for_good(pw, db):
    await one_free_per_week(pw, db)
    async with db.session() as s:
        with pytest.raises(InsufficientCredits):
            await pw.unlock(s, "u1", item(9))
    async with db.session() as s:
        assert await pw.add_credits(s, "u1", ORG, 2) == 2
        assert await pw.unlock(s, "u1", item(9)) is True
        assert await pw.unlock(s, "u1", item(9)) is False  # already open: no second charge
        assert await pw.balance(s, "u1", ORG) == 1
    c = (await read(pw, db, browser(user="u1"), item(1))).set_cookie
    d = await read(pw, db, browser(user="u1", cookie=c), item(9))
    assert d.allowed and d.reason == "unlocked"
    assert not (
        await read(pw, db, browser(user="u1", cookie=c), item(10))
    ).allowed  # other items still walled


async def test_compliance_exports_and_erases_reading_history(pw, db):
    await one_free_per_week(pw, db)
    await read(pw, db, browser(user="u1"), item(1))
    async with db.session() as s:
        await pw.add_credits(s, "u1", ORG, 1)

    class Comp:
        def __init__(self):
            self.ex, self.er = {}, {}

        def register_exporter(self, n, f):
            self.ex[n] = f

        def register_eraser(self, n, f):
            self.er[n] = f

    c = Comp()
    register_with_compliance(c)
    async with db.session() as s:
        out = await c.ex["paywall"](s, "u1")
        assert [r["item"] for r in out["reads"]] == ["recipe-1"] and out["credits"][0][
            "balance"
        ] == 1
        await c.er["paywall"](s, "u1")
    async with db.session() as s:
        for t in ("pw_reads", "pw_credits"):
            assert (await s.execute(text(f"select count(*) from {t}"))).scalar_one() == 0


async def test_the_ip_is_never_stored(pw, db):
    await one_free_per_week(pw, db)
    await read(pw, db, browser(ip="203.0.113.77"), item(1))
    keys = " ".join(pw.cache.d)
    assert "203.0.113" not in keys and "203.0.113" not in " ".join(map(str, pw.cache.d.values()))
    async with db.session() as s:
        dump = (
            (await s.execute(text("select row_to_json(r)::text from pw_reads r"))).scalars().all()
        )
    assert not any("203.0.113" in x for x in dump)


async def test_purge_applies_the_retention_schedule(pw, db):
    await one_free_per_week(pw, db)
    await read(pw, db, browser(user="u1"), item(1))
    async with db.session() as s:
        await s.execute(text("update pw_reads set at = now() - interval '401 days'"))
        assert await pw.purge(s) == 1
        assert (await s.execute(text("select count(*) from pw_reads"))).scalar_one() == 0
