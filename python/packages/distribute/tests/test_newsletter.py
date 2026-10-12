import pytest
from distribute_support import PANCAKES, PANEER
from tsp.distribute import Recipient, digest_context, in_segment, send_digest, templates_dir
from tsp.i18n import Catalog
from tsp.notify import Notifier, Outgoing, Templates

CAT = Catalog()
BASE = "https://food.example.com"


class Transport:
    def __init__(self):
        self.sent: list[Outgoing] = []

    async def send(self, mail):
        self.sent.append(mail)
        return f"m{len(self.sent)}"


@pytest.fixture
def notifier():
    t = Transport()
    n = Notifier(
        t,
        Templates(templates_dir()),
        sender="Food <f@example.com>",
        brand="Food Co",
        base_url=BASE,
        secret="s" * 32,
    )
    n.t = t
    return n


def test_digest_context_is_translated_and_falls_back_per_item():
    hi = digest_context([PANCAKES, PANEER], "hi", BASE, CAT)
    assert (
        hi["subject_line"] == "इस सप्ताह: 2 नई रेसिपी"
        and hi["items"][0]["url"] == f"{BASE}/hi/pancakes"
    )
    assert (
        hi["items"][1]["title"].startswith("Paneer")
        and hi["items"][1]["url"] == f"{BASE}/en/paneer-tikka"
    )  # no Hindi: default


def test_segments():
    sub, free = Recipient("a@x.com", subscriber=True), Recipient("b@x.com")
    assert in_segment(sub, "all") and in_segment(free, "all")
    assert in_segment(sub, "subscribers") and not in_segment(free, "subscribers")
    assert in_segment(free, "free") and not in_segment(sub, "free")


async def test_each_reader_gets_their_language_and_only_consenting_readers_get_mail(db, notifier):
    people = [
        Recipient("en@x.com", "en"),
        Recipient("hi@x.com", "hi", subscriber=True),
        Recipient("ta@x.com", "ta"),  # a language we do not publish in: falls back
        Recipient("no@x.com", "en"),  # never opted in
    ]
    async with db.session() as s:
        for e in ("en@x.com", "hi@x.com", "ta@x.com"):
            await notifier.set_marketing(s, e, True)
        counts = await send_digest(s, notifier, CAT, [PANCAKES], people, languages=["en", "hi"])
    assert counts == {"en": 3, "hi": 1}
    from sqlalchemy import select
    from tsp.notify.models import Email

    async with db.session() as s:
        ids = [m.id for m in (await s.execute(select(Email).order_by(Email.to_addr))).scalars()]
        statuses = {}
        for i in ids:
            statuses[i] = await notifier.deliver(s, i)
    assert sorted(statuses.values()) == ["sent", "sent", "sent", "suppressed"]
    by_to = {m.to: m for m in notifier.t.sent}
    assert set(by_to) == {"en@x.com", "hi@x.com", "ta@x.com"}
    hi = by_to["hi@x.com"]
    assert (
        hi.message.subject == "इस सप्ताह: 1 नई रेसिपी"
        and "रेसिपी पढ़ें" in hi.message.html
        and f"{BASE}/hi/pancakes" in hi.message.html
    )
    assert (
        hi.category == "marketing"
        and "List-Unsubscribe" in hi.headers
        and "unsubscribe" in hi.message.html.lower()
    )
    assert (
        "<img" not in hi.message.html and "track" not in hi.message.html.lower()
    )  # no tracking pixels
    assert by_to["ta@x.com"].message.subject.startswith("This week")


async def test_a_segment_limits_who_is_queued(db, notifier):
    people = [Recipient("a@x.com", subscriber=True), Recipient("b@x.com")]
    async with db.session() as s:
        for p in people:
            await notifier.set_marketing(s, p.email, True)
        assert await send_digest(s, notifier, CAT, [PANCAKES], people, segment="subscribers") == {
            "en": 1
        }
