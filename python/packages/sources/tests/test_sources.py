import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from tsp.audit import AuditLog
from tsp.sources import (
    ApiPage,
    Connector,
    Item,
    JsonApiStrategy,
    PaidApiStrategy,
    SourceChecks,
    SourceRunner,
    Strategy,
    StrategyFailed,
)
from tsp.sources.models import Check, Cursor, Run, Source
from tsp.sources.models import Item as ItemRow
from tsp.sources.runner import SourceDisabled


async def add_source(db, key="demo", **kw):
    async with db.session() as s:
        row = Source(
            key=key,
            name=kw.pop("name", "Demo"),
            url=kw.pop("url", "https://demo.test"),
            strategies=kw.pop("strategies", ["scrape", "api_free"]),
            **kw,
        )
        s.add(row)
    return row


def day(n):
    return datetime(2026, 1, n, tzinfo=timezone.utc)


class Scripted:
    """A strategy driven by a list of items, optionally failing or crashing after N of them."""

    def __init__(self, items, *, fail_after=None, crash_after=None, available=True):
        self.items, self.fail_after, self.crash_after, self._available = (
            items,
            fail_after,
            crash_after,
            available,
        )
        self.calls = []

    async def available(self, ctx):
        return self._available

    async def fetch(self, ctx, stream, since):
        self.calls.append(since)
        for n, item in enumerate(self.items):
            if since and item.updated_at and item.updated_at.isoformat() <= since:
                continue
            if self.fail_after is not None and n >= self.fail_after:
                raise StrategyFailed("site blocked us")
            if self.crash_after is not None and n >= self.crash_after:
                raise RuntimeError("boom")
            yield item


def items(n, content=b"x"):
    return [
        Item(
            external_id=f"id{i}",
            content=content + str(i).encode(),
            content_type="text/plain",
            updated_at=day(i + 1),
        )
        for i in range(n)
    ]


def make_runner(db, store, cache, http, strategies, *, batch_size=2, config=None):
    from tsp.sources import SourcesConfig

    class Conn(Connector):
        key = "demo"
        streams = ["things"]

        def strategies(self):
            return strategies

    from tsp.sources import registry

    registry._CONNECTORS["demo"] = Conn
    return SourceRunner(
        db, store, cache, http, config=config, options=SourcesConfig(batch_size=batch_size)
    )


async def test_fallback_records_strategy_used(db, store, cache, http):
    await add_source(db)
    scrape = Scripted(items(3), fail_after=0)
    api = Scripted(items(3))
    runner = make_runner(db, store, cache, http, {"scrape": scrape, "api_free": api})
    res = await runner.sync("demo")
    r = res.streams[0]
    assert (r.strategy_used, r.status, r.items_new, r.errors) == ("api_free", "ok", 3, 1)
    assert "scrape: site blocked us" in r.error_summary
    async with db.session() as s:
        run = (await s.execute(select(Run))).scalar_one()
        assert run.strategy_used == "api_free" and run.manifest_uri
        assert len((await s.execute(select(ItemRow))).scalars().all()) == 3
    assert (store.dir / "sources/demo/2026/id0.txt").read_bytes() == b"x0"
    lines = [json.loads(x) for x in (store.dir / run.manifest_uri).read_text().splitlines()]
    assert [x["action"] for x in lines] == ["new"] * 3


async def test_fallback_continues_from_committed_cursor(db, store, cache, http):
    await add_source(db)
    scrape = Scripted(items(5), fail_after=3)  # yields id0..id2, then blocked
    api = Scripted(items(5))
    runner = make_runner(db, store, cache, http, {"scrape": scrape, "api_free": api}, batch_size=2)
    res = await runner.sync("demo")
    assert res.streams[0].items_new == 5
    # scrape's partial batch (id2) was never committed, so the API starts after the last committed one
    assert api.calls == [day(2).isoformat()]


async def test_resume_after_crash_has_no_duplicates(db, store, cache, http):
    await add_source(db, strategies=["scrape"])
    crashing = Scripted(items(6), crash_after=5)
    runner = make_runner(db, store, cache, http, {"scrape": crashing}, batch_size=2)
    with pytest.raises(RuntimeError):
        await runner.sync("demo")
    async with db.session() as s:
        cursor = (await s.get(Cursor, (1, "things"))).value
        assert cursor == day(4).isoformat()  # batches 1 and 2 committed (id0..id3)
        assert len((await s.execute(select(ItemRow))).scalars().all()) == 4
        run = (await s.execute(select(Run))).scalar_one()
        assert run.status == "partial" and "boom" in run.error_summary

    healthy = Scripted(items(6))
    runner = make_runner(db, store, cache, http, {"scrape": healthy}, batch_size=2)
    res = await runner.sync("demo")
    assert healthy.calls == [day(4).isoformat()]
    assert res.streams[0].items_new == 2
    async with db.session() as s:
        ids = (await s.execute(select(ItemRow.external_id))).scalars().all()
        assert sorted(ids) == [f"id{i}" for i in range(6)]


async def test_unchanged_items_are_not_rewritten(db, store, cache, http):
    await add_source(db, strategies=["scrape"])
    strat = Scripted(items(2))
    runner = make_runner(db, store, cache, http, {"scrape": strat})
    await runner.sync("demo")
    (store.dir / "sources/demo/2026/id0.txt").write_bytes(b"tampered")
    res = await runner.sync("demo", since="")  # re-read everything
    assert (res.items_new, res.items_updated) == (0, 0)
    assert (store.dir / "sources/demo/2026/id0.txt").read_bytes() == b"tampered"  # not rewritten
    strat.items = items(2, content=b"y")
    res = await runner.sync("demo", since="")
    assert res.items_updated == 2
    assert (store.dir / "sources/demo/2026/id0.txt").read_bytes() == b"y0"


async def test_dry_run_writes_nothing(db, store, cache, http):
    await add_source(db, strategies=["scrape"])
    runner = make_runner(db, store, cache, http, {"scrape": Scripted(items(3))})
    res = await runner.sync("demo", dry_run=True)
    assert res.items_new == 3
    async with db.session() as s:
        assert (await s.execute(select(ItemRow))).first() is None
        assert (await s.execute(select(Run))).first() is None
        assert await s.get(Cursor, (1, "things")) is None
    assert not store.dir.exists()


async def test_unavailable_and_unknown_strategies_are_skipped(db, store, cache, http):
    await add_source(db, strategies=["bulk", "scrape", "api_free"])
    runner = make_runner(
        db,
        store,
        cache,
        http,
        {"scrape": Scripted(items(1), available=False), "api_free": Scripted(items(1))},
    )
    r = (await runner.sync("demo")).streams[0]
    assert r.strategy_used == "api_free" and r.errors == 2
    assert (
        "bulk: connector has no such strategy" in r.error_summary
        and "scrape: unavailable" in r.error_summary
    )


async def test_all_strategies_failing_is_a_failed_run(db, store, cache, http):
    await add_source(db)
    runner = make_runner(
        db,
        store,
        cache,
        http,
        {
            "scrape": Scripted(items(2), fail_after=0),
            "api_free": Scripted([], fail_after=0, available=False),
        },
    )
    r = (await runner.sync("demo")).streams[0]
    assert r.status == "failed" and r.strategy_used is None


async def test_disabled_source_is_refused(db, store, cache, http):
    await add_source(db, enabled=False)
    runner = make_runner(db, store, cache, http, {"scrape": Scripted([])})
    with pytest.raises(SourceDisabled):
        await runner.sync("demo")


class Fixed(Strategy):  # minimal real Strategy for the paid wrapper
    def __init__(self, n):
        self.n = n

    async def available(self, ctx):
        return True

    async def fetch(self, ctx, stream, since):
        for i in range(self.n):
            yield Item(external_id=f"p{i}", content=b"p", content_type="text/plain")


async def test_paid_cap_stops_run_and_meters_usage(db, store, cache, http):
    await add_source(db, strategies=["api_paid"], paid_cap_monthly=Decimal("0.25"))
    runner = make_runner(db, store, cache, http, {"api_paid": PaidApiStrategy(Fixed(10), "0.10")})
    r = (await runner.sync("demo")).streams[0]
    assert (r.items_new, r.status, r.cost) == (2, "partial", Decimal("0.20"))
    # The month's spend now leaves 0.05: the paid strategy reports unavailable on the next run.
    again = (await runner.sync("demo")).streams[0]
    assert (
        again.items_new == 0
        and again.status == "failed"
        and "api_paid: unavailable" in again.error_summary
    )
    from tsp.metering import UsageRecord

    async with db.session() as s:
        usage = (await s.execute(select(UsageRecord))).scalars().all()
        assert [(u.kind, u.org_id, u.units_in) for u in usage] == [
            ("sources.paid_call", "platform", 2)
        ]


async def test_paid_without_cap_is_unavailable(db, store, cache, http):
    await add_source(db, strategies=["api_paid"])
    runner = make_runner(db, store, cache, http, {"api_paid": PaidApiStrategy(Fixed(3), "0.01")})
    assert (await runner.sync("demo")).streams[0].status == "failed"


async def test_json_api_strategy_pages_and_sends_key(db, store, cache, http, site):
    await add_source(db, strategies=["api_free"], url="https://demo.test")
    site.json("/api/things", {"results": [{"id": 1}], "next": "2"})
    site.json("/api/things?page=2", {"results": [{"id": 2}], "next": None})
    api = JsonApiStrategy(
        lambda stream, since, token: (
            f"https://demo.test/api/{stream}" + (f"?page={token}" if token else "")
        ),
        lambda doc: ApiPage(
            [
                Item(external_id=str(r["id"]), content=b"{}", content_type="application/json")
                for r in doc["results"]
            ],
            doc["next"],
        ),
        auth_key="demo_key",
    )
    runner = make_runner(db, store, cache, http, {"api_free": api}, config={})
    assert (await runner.sync("demo")).streams[
        0
    ].status == "failed"  # no key configured: unavailable
    runner = make_runner(db, store, cache, http, {"api_free": api}, config={"demo_key": "sekret"})
    r = (await runner.sync("demo")).streams[0]
    assert r.items_new == 2
    api_calls = [a for path, a in zip(site.log, site.auth, strict=True) if path != "/robots.txt"]
    assert api_calls == ["Bearer sekret", "Bearer sekret"]  # and the key never went to robots.txt


async def test_reconcile_compares_two_strategies(db, store, cache, http):
    await add_source(db)
    a = [
        Item(external_id=i, content=b"1", updated_at=day(n))
        for n, i in enumerate(["a", "b", "c"], 1)
    ]
    b = [
        Item(external_id="a", content=b"2", updated_at=day(1)),
        Item(external_id="b", content=b"2", updated_at=day(9)),
        Item(external_id="d", content=b"2"),
    ]
    runner = make_runner(db, store, cache, http, {"scrape": Scripted(a), "api_free": Scripted(b)})
    rep = await runner.reconcile("demo", "things")
    assert (rep.only_in_first, rep.only_in_second, rep.different, rep.compared) == (
        ["c"],
        ["d"],
        ["b"],
        2,
    )
    assert not rep.consistent


async def test_check_disables_source_when_terms_change(db, store, cache, http, site):
    await add_source(db, strategies=["scrape"], terms_url="https://demo.test/terms")
    site.routes["/"] = (200, "text/html", b"<html><body>hi</body></html>")
    terms = (
        "<html><head><title>Terms</title></head><body><main><p>"
        + "You may read our pages for personal use. " * 12
        + "</p></main></body></html>"
    )
    site.routes["/terms"] = (200, "text/html", terms.encode())
    checks = SourceChecks(db, http, cache)
    first = await checks.check("demo")
    assert first.reachable and first.robots_ok is True and not first.disabled and first.terms_hash

    site.routes["/terms"] = (
        200,
        "text/html",
        terms.replace("personal use", "no use at all").encode(),
    )
    second = await checks.check("demo")
    assert second.disabled and second.reasons == ["terms page changed"]
    async with db.session() as s:
        src = (await s.execute(select(Source))).scalar_one()
        assert src.enabled is False
        audit = (await s.execute(select(AuditLog))).scalars().all()
        assert [(a.action, a.target_id) for a in audit] == [("sources.disabled", "demo")]
        assert len((await s.execute(select(Check))).scalars().all()) == 2


async def test_check_disables_when_robots_starts_disallowing_but_not_on_outage(
    db, store, cache, http, site
):
    await add_source(db, strategies=["scrape"])
    site.routes["/"] = (200, "text/html", b"<html></html>")
    checks = SourceChecks(db, http, cache)
    site.routes["/robots.txt"] = (503, "text/plain", b"")
    out = await checks.check("demo")
    assert out.robots_ok is None and not out.disabled  # outage: recorded, not acted on
    site.routes["/robots.txt"] = (200, "text/plain", b"User-agent: *\nDisallow: /\n")
    res = await checks.check("demo")
    assert res.disabled and res.reasons == ["robots.txt now disallows our crawler"]
