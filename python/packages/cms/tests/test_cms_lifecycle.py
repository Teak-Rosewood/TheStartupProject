"""K4-B: the corrections log, the review cadence and automatic freezing (frozen clock throughout)."""

from datetime import timedelta

import httpx
import pytest
from cms_support import ORG, OTHER
from sqlalchemy import select, text
from tsp.audit import Actor, AuditLog
from tsp.cms import (
    CmsError,
    CmsService,
    Item,
    LifecycleConfig,
    Review,
    build_public_router,
    is_refreshable,
    register_jobs,
)
from tsp.jobs import JobRegistry, Worker
from tsp.server import Settings, create_app

ANN, BO = Actor("ann", "editor"), Actor("bo", "reviewer")
BODY = [{"type": "paragraph", "text": "Whisk the eggs."}]


@pytest.fixture
def notified():
    return []


@pytest.fixture
def svc(session, clock, notified):
    async def notify(session, item, deadline):
        notified.append((item.slug, deadline))

    return CmsService(session, clock=clock, notify_review_due=notify)


async def published(svc, slug="pancakes", kind_type="recipe"):
    item = await svc.create(ORG, kind_type, slug, "ann")
    await svc.save_version(ORG, item.id, "en", title="Pancakes", body=BODY, created_by="ann")
    await svc.submit(ORG, item.id, ANN)
    await svc.approve(ORG, item.id, BO)
    return await svc.publish(ORG, item.id, BO)


async def revise(svc, item, title, **publish_kw):
    await svc.save_version(ORG, item.id, "en", title=title, body=BODY, created_by="ann")
    await svc.submit(ORG, item.id, ANN)
    await svc.approve(ORG, item.id, BO)
    return await svc.publish(ORG, item.id, BO, **publish_kw)


# -- the corrections log -----------------------------------------------------------------------


async def test_the_first_publish_needs_no_note_but_a_republish_does(svc):
    item = await published(svc)
    assert await svc.corrections(ORG, "pancakes") == []
    first = item.published_version_id
    await svc.save_version(ORG, item.id, "en", title="Pancakes v2", body=BODY, created_by="ann")
    await svc.submit(ORG, item.id, ANN)
    await svc.approve(ORG, item.id, BO)
    with pytest.raises(CmsError, match="needs a kind"):
        await svc.publish(ORG, item.id, BO)
    with pytest.raises(CmsError, match="needs a kind"):
        await svc.publish(ORG, item.id, BO, kind="oops", public_note="x")
    with pytest.raises(CmsError, match="public note"):
        await svc.publish(ORG, item.id, BO, kind="correction", public_note="  ")
    assert (await svc.get(ORG, item.id)).published_version_id == first  # nothing went live
    done = await svc.publish(
        ORG,
        item.id,
        BO,
        kind="correction",
        public_note="The batter rests 5 minutes, not 50.",
        internal_note="typo, Asha",
    )
    (c,) = await svc.corrections(ORG, "pancakes")
    assert (
        c["kind"] == "correction"
        and c["note"].startswith("The batter rests")
        and c["title"] == "Pancakes v2"
    )
    assert c["from_version"] == str(first) and c["to_version"] == str(done.published_version_id)
    assert "internal" not in str(c) and "typo" not in str(c)  # the internal note never leaves


async def test_a_scheduled_republish_carries_its_note(svc, session, clock):
    item = await published(svc)
    await svc.save_version(ORG, item.id, "en", title="Pancakes v2", body=BODY, created_by="ann")
    await svc.submit(ORG, item.id, ANN)
    await svc.approve(ORG, item.id, BO)
    with pytest.raises(CmsError, match="needs a kind"):
        await svc.schedule(ORG, item.id, clock() + timedelta(hours=1), BO)
    await svc.schedule(
        ORG,
        item.id,
        clock() + timedelta(hours=1),
        BO,
        kind="update",
        public_note="Added a gluten-free variation.",
    )
    clock.at += timedelta(hours=2)
    assert await svc.publish_due() == [item.id]
    (c,) = await svc.corrections(ORG, "pancakes")
    assert c["kind"] == "update" and "gluten-free" in c["note"]
    assert "pending_correction" not in (await svc.get(ORG, item.id)).attrs


async def test_notes_without_a_new_version_and_log_scoping(svc):
    item = await published(svc)
    other = await published(svc, slug="waffles")
    await svc.add_note(ORG, item.id, "clarification", "Plain flour means all-purpose flour.", BO)
    await svc.add_note(ORG, other.id, "update", "Now with a video.", BO)
    assert [c["slug"] for c in await svc.corrections(ORG)] == [
        "waffles",
        "pancakes",
    ]  # newest first
    assert len(await svc.corrections(ORG, "pancakes")) == 1
    assert await svc.corrections(OTHER) == []
    with pytest.raises(CmsError, match="public note"):
        await svc.add_note(ORG, item.id, "update", "", BO)
    await svc.unpublish(ORG, other.id, "withdrawn", BO)
    assert [c["slug"] for c in await svc.corrections(ORG)] == [
        "pancakes"
    ]  # unpublished items vanish from the log


# -- the review cadence ------------------------------------------------------------------------------


async def test_publishing_sets_the_review_date_per_type(session, clock):
    cfg = LifecycleConfig(review_after_days=30, auto_freeze_after_days=7, by_type={"news": (7, 3)})
    svc = CmsService(session, clock=clock, lifecycle=cfg)
    recipe = await published(svc, "r")
    news = await published(svc, "n", "news")
    assert recipe.review_due_at == clock() + timedelta(days=30)
    assert news.review_due_at == clock() + timedelta(days=7)
    assert recipe.lifecycle == "open"


async def test_a_due_review_notifies_once_and_freezes_after_the_grace_period(
    svc, session, clock, notified
):
    item = await published(svc)
    clock.at += timedelta(days=29)
    assert await svc.run_lifecycle() == {"notified": [], "frozen": []}  # not due yet
    clock.at += timedelta(days=2)  # day 31: due
    assert (await svc.run_lifecycle())["notified"] == [item.id]
    assert await svc.run_lifecycle() == {"notified": [], "frozen": []}  # running again is harmless
    assert len(notified) == 1 and notified[0][1] == item.review_due_at + timedelta(days=7)
    assert (await session.execute(select(Review))).scalars().one().decided_at is None
    clock.at += timedelta(days=5)  # day 36: still inside the grace period (ends day 37)
    assert (await svc.run_lifecycle())["frozen"] == []
    assert is_refreshable(await svc.get(ORG, item.id))
    clock.at += timedelta(days=3)  # day 39: grace is over
    assert (await svc.run_lifecycle())["frozen"] == [item.id]
    frozen = await svc.get(ORG, item.id)
    assert (frozen.lifecycle, frozen.frozen_reason, frozen.review_due_at) == (
        "frozen",
        "auto",
        None,
    )
    assert frozen.frozen_at == clock() and not is_refreshable(frozen)
    (c,) = await svc.corrections(ORG, "pancakes")
    assert (
        c["kind"] == "update"
        and c["note"] == "This article is no longer updated as of 9 April 2026."
    )
    rev = (await session.execute(select(Review))).scalars().one()
    assert (rev.decision, rev.decided_by) == ("frozen", "system")
    assert "cms.item.freeze" in [
        a.action for a in (await session.execute(select(AuditLog))).scalars()
    ]
    assert await svc.run_lifecycle() == {
        "notified": [],
        "frozen": [],
    }  # frozen items are left alone


async def test_a_living_decision_starts_a_new_cycle_and_blocks_the_freeze(svc, clock):
    item = await published(svc)
    clock.at += timedelta(days=31)
    await svc.run_lifecycle()
    clock.at += timedelta(days=3)
    done = await svc.decide_review(ORG, item.id, "living", BO, "still accurate")
    assert done.lifecycle == "living" and done.review_due_at == clock() + timedelta(days=30)
    clock.at += timedelta(days=10)  # past the original grace period
    assert await svc.run_lifecycle() == {"notified": [], "frozen": []}
    clock.at += timedelta(days=21)  # the next review is due...
    assert (await svc.run_lifecycle())["notified"] == [item.id]
    clock.at += timedelta(days=8)  # ...and nobody decides: living items freeze too
    assert (await svc.run_lifecycle())["frozen"] == [item.id]


async def test_an_editor_can_freeze_early_and_unfreeze_later(svc, clock):
    item = await published(svc)
    done = await svc.decide_review(ORG, item.id, "frozen", BO, "recipe is retired")
    assert done.lifecycle == "frozen" and done.frozen_reason == "recipe is retired"
    with pytest.raises(CmsError, match="already frozen"):
        await svc.decide_review(ORG, item.id, "living", BO)
    with pytest.raises(CmsError, match="decision must be"):
        await svc.decide_review(ORG, item.id, "maybe", BO)
    with pytest.raises(CmsError, match="needs a reason"):
        await svc.unfreeze(ORG, item.id, BO, " ")
    back = await svc.unfreeze(ORG, item.id, BO, "kitchen retested it")
    assert (
        back.lifecycle == "living"
        and back.frozen_at is None
        and back.review_due_at == clock() + timedelta(days=30)
    )
    with pytest.raises(CmsError, match="not frozen"):
        await svc.unfreeze(ORG, item.id, BO, "again")


async def test_corrections_still_work_on_a_frozen_item_and_do_not_restart_reviews(svc, clock):
    item = await published(svc)
    await svc.decide_review(ORG, item.id, "frozen", BO, "retired")
    done = await revise(
        svc, item, "Pancakes (fixed)", kind="correction", public_note="Oven temperature was wrong."
    )
    assert done.lifecycle == "frozen" and done.review_due_at is None
    assert [c["kind"] for c in await svc.corrections(ORG, "pancakes")] == ["correction", "update"]


async def test_unpublished_and_draft_items_are_never_reviewed(svc, clock):
    item = await published(svc)
    await svc.unpublish(ORG, item.id, "withdrawn", BO)
    assert (await svc.get(ORG, item.id)).review_due_at is None
    draft = await svc.create(ORG, "recipe", "later", "ann")
    clock.at += timedelta(days=100)
    assert await svc.run_lifecycle() == {"notified": [], "frozen": []}
    assert not is_refreshable(draft) and not is_refreshable(await svc.get(ORG, item.id))


async def test_the_freeze_note_follows_the_items_language(session, clock):
    svc = CmsService(session, clock=clock)
    item = await svc.create(ORG, "recipe", "hi-only", "ann", primary_lang="hi")
    await svc.save_version(ORG, item.id, "hi", title="पैनकेक", body=BODY, created_by="ann")
    await svc.submit(ORG, item.id, ANN)
    await svc.approve(ORG, item.id, BO)
    await svc.publish(ORG, item.id, BO)
    await svc.decide_review(ORG, item.id, "frozen", BO)
    (c,) = await svc.corrections(ORG, "hi-only")
    assert "अपडेट नहीं" in c["note"]


async def test_the_daily_job_runs_the_lifecycle(db, clock, notified):
    async with db.session() as s:
        svc = CmsService(s, clock=clock)
        item = await published(svc)
    clock.at += timedelta(days=40)
    reg = JobRegistry()
    register_jobs(reg, clock=clock)
    assert "cms.lifecycle" in reg.handlers and reg.schedules["cms-lifecycle"][1] == "cms.lifecycle"
    from tsp.jobs import enqueue

    async with db.session() as s:
        await enqueue(s, "cms.lifecycle")
    assert await Worker(reg, db, poll_interval=0.05).run_once() == 1
    async with db.session() as s:
        assert (await s.get(Item, item.id)).lifecycle == "frozen"


# -- public log and feed -----------------------------------------------------------------------------


async def test_public_log_and_rss(db, clock):
    async with db.session() as s:
        svc = CmsService(s, clock=clock)
        item = await published(svc)
        await svc.add_note(
            ORG,
            item.id,
            "correction",
            "Salt is 1 tsp & not 1 tbsp.",
            BO,
            internal_note="reader email",
        )
        await published(svc, "draft-ish")  # no corrections: must not appear
    app = create_app(Settings())
    app.state.db = db
    from tsp.server import db_session

    app.include_router(
        build_public_router(
            db_session, ORG, base_url="https://food.example.com", title="Food corrections"
        )
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        feed = await c.get("/public/cms/corrections")
        assert feed.status_code == 200 and feed.headers["cache-control"].startswith("public")
        (entry,) = feed.json()["corrections"]
        assert entry["slug"] == "pancakes" and "reader email" not in feed.text
        assert (
            (await c.get("/public/cms/items/pancakes/corrections"))
            .json()["corrections"][0]["note"]
            .startswith("Salt")
        )
        assert (await c.get("/public/cms/items/draft-ish/corrections")).json() == {
            "corrections": []
        }
        assert (await c.get("/public/cms/items/nope/corrections")).status_code == 404
        rss = await c.get("/public/cms/corrections.rss")
        assert rss.headers["content-type"].startswith("application/rss+xml")
        import xml.etree.ElementTree as ET

        root = ET.fromstring(rss.text)
        (it,) = root.findall("./channel/item")
        assert (
            it.find("title").text == "Correction: Pancakes" and "&" in it.find("description").text
        )
        assert it.find("link").text == "https://food.example.com/pancakes"
        assert (
            ET.fromstring((await c.get("/public/cms/items/pancakes/corrections.rss")).text)
            .find("./channel/title")
            .text
            == "Food corrections: pancakes"
        )


async def test_the_migration_adds_only_new_tables(db):
    async with db.session() as s:
        names = (
            (
                await s.execute(
                    text(
                        "select table_name from information_schema.tables where table_name like 'cms_%'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {"cms_corrections", "cms_reviews"} <= set(names)
        # one open review per item
        item_id = (
            await s.execute(
                text("insert into cms_items (org_id, type, slug) values ('o','t','s') returning id")
            )
        ).scalar_one()
        await s.execute(
            text("insert into cms_reviews (item_id, due_at) values (:i, now())"), {"i": item_id}
        )
    with pytest.raises(Exception, match="uq_cms_reviews_open"):
        async with db.session() as s:
            await s.execute(
                text("insert into cms_reviews (item_id, due_at) values (:i, now())"), {"i": item_id}
            )


async def test_editor_api_publish_needs_the_note_and_review_decisions(db, clock):
    from fastapi.testclient import TestClient  # noqa: F401  (kept for parity with the publishing tests)
    from tsp.cms import Principal, build_router
    from tsp.server import db_session

    who = {"p": Principal(ORG, "ann", frozenset({"editor"}))}

    async def principal():
        return who["p"]

    app = create_app(Settings())
    app.state.db = db
    app.include_router(
        build_router(principal, db_session, lambda s: CmsService(s, clock=clock)), prefix="/api"
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        item = (await c.post("/api/cms/items", json={"type": "recipe", "slug": "pancakes"})).json()
        iid = item["id"]
        await c.post(
            f"/api/cms/items/{iid}/versions", json={"lang": "en", "title": "P", "body": BODY}
        )
        await c.post(f"/api/cms/items/{iid}/submit")
        who["p"] = Principal(ORG, "bo", frozenset({"reviewer"}))
        await c.post(f"/api/cms/items/{iid}/approve")
        pub = await c.post(f"/api/cms/items/{iid}/publish")
        assert pub.status_code == 200 and pub.json()["review_due_at"]
        who["p"] = Principal(ORG, "ann", frozenset({"editor"}))
        await c.post(
            f"/api/cms/items/{iid}/versions", json={"lang": "en", "title": "P2", "body": BODY}
        )
        await c.post(f"/api/cms/items/{iid}/submit")
        who["p"] = Principal(ORG, "bo", frozenset({"reviewer"}))
        await c.post(f"/api/cms/items/{iid}/approve")
        assert (await c.post(f"/api/cms/items/{iid}/publish")).status_code == 409
        ok = await c.post(
            f"/api/cms/items/{iid}/publish",
            json={"kind": "correction", "public_note": "Fixed the title."},
        )
        assert ok.status_code == 200
        assert (
            await c.post(
                f"/api/cms/items/{iid}/review", json={"decision": "frozen", "reason": "retired"}
            )
        ).json()["lifecycle"] == "frozen"
        assert (await c.post(f"/api/cms/items/{iid}/unfreeze", json={"reason": "retested"})).json()[
            "lifecycle"
        ] == "living"
        n = await c.post(
            f"/api/cms/items/{iid}/notes",
            json={"kind": "clarification", "public_note": "Eggs are large."},
        )
        assert n.status_code == 201
        who["p"] = Principal(ORG, "ann", frozenset({"editor"}))
        assert (
            await c.post(f"/api/cms/items/{iid}/review", json={"decision": "living"})
        ).status_code == 403
