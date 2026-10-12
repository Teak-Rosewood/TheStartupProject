from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select
from tsp.jobs import JobRegistry
from tsp.sources.models import Item as SourceItem
from tsp.sources.models import Source
from tsp.trends import (
    CandidateRow,
    Conflict,
    CustomFeedSignal,
    FeedVolumeSignal,
    NotFound,
    SignalRow,
    TrendsConfig,
    TrendsService,
    build_router,
    register_jobs,
)

DAY = date(2026, 3, 1)
END = datetime(2026, 3, 2, tzinfo=timezone.utc)  # end of DAY
ORG, OTHER = "org1", "org2"


def make(db, web, signals=None, **kw):
    return TrendsService(
        db, web.fetcher(), signals or {}, TrendsConfig(**kw.pop("config", {})), **kw
    )


async def seed_topics(svc, db, org=ORG):
    async with db.session() as s:
        fryer = await svc.upsert_topic(s, org, "air-fryer", "Air fryer", ["air fryer"])
        ramen = await svc.upsert_topic(s, org, "ramen", "Ramen", ["ramen"])
    return fryer.id, ramen.id


async def seed_signal(db, org, topic_id, signal, today, base=10, label=None, days=28):
    """``days`` of flat history at ``base`` per day, then ``today`` on the scored day."""
    attrs = {"label": label} if label else {}
    async with db.session() as s:
        for d in range(0, days + 1):
            s.add(
                SignalRow(
                    org_id=org,
                    topic_id=topic_id,
                    signal=signal,
                    at=END - timedelta(days=d),
                    value=Decimal(today if d == 0 else base + (d % 3)),
                    attrs=attrs,
                )
            )


async def test_scan_ranks_a_spike_and_ignores_quiet_topics(db, web):
    svc = make(db, web)
    fryer, ramen = await seed_topics(svc, db)
    await seed_signal(db, ORG, fryer, "feed_volume", today=80)
    await seed_signal(db, ORG, ramen, "feed_volume", today=11)
    rows = await svc.scan(ORG, DAY)
    assert [r.label for r in rows] == ["Air fryer"]
    assert (
        rows[0].status == "new"
        and rows[0].components["feed_volume"] > 5
        and rows[0].topic_id == fryer
    )


async def test_signals_add_up_with_weights(db, web):
    svc = make(db, web, config={"weights": {"gdelt_volume": 0.5}})
    fryer, _ = await seed_topics(svc, db)
    await seed_signal(db, ORG, fryer, "feed_volume", today=80)
    solo = (await svc.scan(ORG, DAY))[0].score
    await seed_signal(db, ORG, fryer, "gdelt_volume", today=80)
    both = (await svc.scan(ORG, DAY))[0]
    assert both.score > solo and set(both.components) == {"feed_volume", "gdelt_volume"}


async def test_emerging_term_becomes_a_candidate_without_a_topic(db, web):
    svc = make(db, web)
    await seed_topics(svc, db)
    await seed_signal(db, ORG, None, "emerging_terms", today=40, label="tamarind candy")
    (row,) = await svc.scan(ORG, DAY)
    assert (row.label, row.topic_id) == ("tamarind candy", None)


async def test_near_identical_labels_are_merged(db, web):
    svc = make(db, web)
    await seed_topics(svc, db)
    await seed_signal(db, ORG, None, "emerging_terms", today=40, label="Tamarind Candy")
    await seed_signal(db, ORG, None, "emerging_terms", today=30, label="tamarind  candy")
    assert len(await svc.scan(ORG, DAY)) == 1


async def test_embedding_dedupe_and_notify(db, web):
    seen = []

    async def embed(texts):
        return [[1.0, 0.0] if "candy" in t else [0.0, 1.0] for t in texts]

    async def notify(org, day, rows):
        seen.append((org, day, sorted(r.label for r in rows)))

    svc = make(db, web, embed=embed, notify=notify)
    await seed_topics(svc, db)
    await seed_signal(db, ORG, None, "emerging_terms", today=40, label="tamarind candy")
    await seed_signal(db, ORG, None, "emerging_terms", today=40, label="candy of tamarind")
    await seed_signal(db, ORG, None, "emerging_terms", today=40, label="kimchi jar")
    rows = await svc.scan(ORG, DAY)
    assert len(rows) == 2
    assert seen == [(ORG, DAY, sorted(r.label for r in rows))]
    await svc.scan(ORG, DAY)
    assert len(seen) == 1  # a re-scan adds nothing new, so nobody is notified twice


async def test_rescan_keeps_decisions_and_updates_new_scores(db, web):
    svc = make(db, web)
    fryer, ramen = await seed_topics(svc, db)
    await seed_signal(db, ORG, fryer, "feed_volume", today=80)
    await seed_signal(db, ORG, ramen, "feed_volume", today=90)
    rows = await svc.scan(ORG, DAY)
    by = {r.label: r for r in rows}
    async with db.session() as s:
        await svc.pick(s, ORG, by["Ramen"].id, "ed", "lead story")
        await svc.dismiss(s, ORG, by["Air fryer"].id, "ed")
    async with db.session() as s:
        s.add(
            SignalRow(
                org_id=ORG,
                topic_id=ramen,
                signal="feed_volume",
                at=END - timedelta(hours=1),
                value=Decimal(500),
            )
        )
    again = {r.label: r for r in await svc.scan(ORG, DAY)}
    assert again["Ramen"].status == "picked" and again["Ramen"].score == by["Ramen"].score
    assert again["Ramen"].picked_by == "ed" and again["Ramen"].note == "lead story"
    assert again["Air fryer"].status == "dismissed"


async def test_decisions_are_org_scoped_and_final(db, web):
    svc = make(db, web)
    fryer, _ = await seed_topics(svc, db)
    await seed_signal(db, ORG, fryer, "feed_volume", today=80)
    (row,) = await svc.scan(ORG, DAY)
    async with db.session() as s:
        with pytest.raises(NotFound):
            await svc.pick(s, OTHER, row.id, "mallory")
        await svc.pick(s, ORG, row.id, "ed")
        with pytest.raises(Conflict):
            await svc.dismiss(s, ORG, row.id, "ed")
        assert await svc.candidates(s, OTHER, DAY) == []


async def test_inactive_topics_are_not_scored(db, web):
    svc = make(db, web)
    fryer, _ = await seed_topics(svc, db)
    await seed_signal(db, ORG, fryer, "feed_volume", today=80)
    async with db.session() as s:
        await svc.upsert_topic(s, ORG, "air-fryer", "Air fryer", ["air fryer"], active=False)
    assert await svc.scan(ORG, DAY) == []


async def test_orgs_do_not_see_each_others_signals(db, web):
    svc = make(db, web)
    fryer, _ = await seed_topics(svc, db)
    await seed_topics(svc, db, OTHER)
    await seed_signal(db, ORG, fryer, "feed_volume", today=80)
    assert await svc.scan(OTHER, DAY) == []


def rss(*titles, at):
    items = "".join(
        f"<item><title>{t}</title><link>https://n.test/{i}</link><pubDate>{at}</pubDate></item>"
        for i, t in enumerate(titles)
    )
    return f"<rss><channel>{items}</channel></rss>"


async def test_collect_stores_points_and_replaces_the_overlap(db, web):
    now = datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc)
    stamp = "Sun, 01 Mar 2026 12:10:00 +0000"
    web.pages["https://f.test/rss"] = (200, rss("air fryer tips", "air fryer deals", at=stamp))
    svc = make(db, web, {"feed_volume": FeedVolumeSignal(["https://f.test/rss"])})
    await seed_topics(svc, db)
    assert await svc.collect(ORG, "feed_volume", now=now) == 1
    assert (
        await svc.collect(ORG, "feed_volume", now=now) == 1
    )  # same hour again: replaced, not doubled
    async with db.session() as s:
        total = (await s.execute(select(func.sum(SignalRow.value)))).scalar_one()
    assert total == 2


async def test_failing_signal_is_skipped_in_collect_all(db, web):
    class Boom:
        name = "boom"

        async def collect(self, ctx, topics, since):
            raise RuntimeError("down")

    web.pages["https://f.test/rss"] = (200, rss("ramen", at="Sun, 01 Mar 2026 12:10:00 +0000"))
    svc = make(db, web, {"boom": Boom(), "feed_volume": FeedVolumeSignal(["https://f.test/rss"])})
    await seed_topics(svc, db)
    done = await svc.collect_all(now=datetime(2026, 3, 1, 13, tzinfo=timezone.utc))
    assert done == {f"{ORG}/feed_volume": 1}


async def test_custom_feed_signal_reads_a_sources_stream(db, web):
    async with db.session() as s:
        src = Source(key="blog", name="Blog", url="https://b.test", strategies=["scrape"])
        s.add(src)
        await s.flush()
        at = datetime(2026, 3, 1, 9, 5, tzinfo=timezone.utc)
        for i, title in enumerate(["Ramen at home", "Best ramen shops", "Garden tips"]):
            s.add(
                SourceItem(
                    source_id=src.id,
                    stream="posts",
                    external_id=str(i),
                    checksum="x",
                    fetched_at=at,
                    meta={"title": title},
                )
            )
    svc = make(db, web, {"source:blog": CustomFeedSignal("blog", "posts")})
    await seed_topics(svc, db)
    assert (
        await svc.collect(ORG, "source:blog", now=datetime(2026, 3, 1, 12, tzinfo=timezone.utc))
        == 1
    )
    async with db.session() as s:
        (row,) = (await s.execute(select(SignalRow))).scalars().all()
    assert row.value == 2 and row.signal == "source:blog"


async def test_prune_drops_old_signals(db, web):
    svc = make(db, web, config={"keep_days": 30})
    fryer, _ = await seed_topics(svc, db)
    await seed_signal(db, ORG, fryer, "feed_volume", today=1, days=60)
    await svc.prune(now=END)
    async with db.session() as s:
        oldest = (await s.execute(select(func.min(SignalRow.at)))).scalar_one()
    assert oldest >= END - timedelta(days=30)


async def test_jobs_register_and_run(db, web):
    web.pages["https://f.test/rss"] = (200, rss("ramen", at="Sun, 01 Mar 2026 12:10:00 +0000"))
    svc = make(db, web, {"feed_volume": FeedVolumeSignal(["https://f.test/rss"])})
    await seed_topics(svc, db)
    reg = JobRegistry()
    register_jobs(reg, svc)
    assert set(reg.handlers) == {"trends.collect", "trends.scan", "trends.prune"}
    assert set(reg.schedules) == {"trends-collect", "trends-scan", "trends-prune"}
    await reg.handlers["trends.scan"](None, {})
    await reg.handlers["trends.prune"](None, {})


@pytest.fixture
async def api(db, web):
    svc = make(db, web)
    fryer, _ = await seed_topics(svc, db)
    await seed_signal(db, ORG, fryer, "feed_volume", today=80)
    await svc.scan(ORG, DAY)

    async def session():
        async with db.session() as s:
            yield s

    def member(role):
        async def dep(org_id: str):
            if org_id != ORG:
                raise HTTPException(403, "not a member")

        return dep

    app = FastAPI()
    app.include_router(build_router(svc, lambda: SimpleNamespace(sub="ed"), member, session))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_api_list_pick_dismiss(api):
    base = f"/orgs/{ORG}/trends/candidates"
    (cand,) = (await api.get(base, params={"day": DAY.isoformat()})).json()
    assert cand["label"] == "Air fryer" and cand["status"] == "new"
    picked = await api.post(f"{base}/{cand['id']}/pick", json={"note": "monday"})
    assert picked.json()["status"] == "picked" and picked.json()["picked_by"] == "ed"
    again = await api.post(f"{base}/{cand['id']}/dismiss")
    assert again.status_code == 409
    assert (await api.post(f"{base}/nope/pick")).status_code == 404
    assert (await api.get("/orgs/other/trends/candidates")).status_code == 403


async def test_audit_entry_written_for_decision(db, web, api):
    from tsp.audit import AuditLog

    base = f"/orgs/{ORG}/trends/candidates"
    (cand,) = (await api.get(base, params={"day": DAY.isoformat()})).json()
    await api.post(f"{base}/{cand['id']}/dismiss")
    async with db.session() as s:
        assert (await s.execute(select(AuditLog.action))).scalars().all() == [
            "trends.candidate_dismissed"
        ]
    assert isinstance(CandidateRow.__tablename__, str)
