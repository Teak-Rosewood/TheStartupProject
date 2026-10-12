import uuid
from datetime import timedelta

import pytest
from cms_support import ORG, OTHER, URL
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from tsp.audit import Actor, AuditLog
from tsp.cms import (
    AllVariantsReady,
    CheckResult,
    CmsError,
    CmsService,
    Item,
    Principal,
    PublishBlocked,
    build_router,
    default_registry,
    register_jobs,
)
from tsp.db import Database
from tsp.jobs import JobRegistry, Worker

ANN, BO = Actor("ann", "editor"), Actor("bo", "reviewer")
BODY = [
    {"type": "paragraph", "text": "Whisk the eggs."},
    {"type": "paragraph", "text": "Fold in flour."},
]


@pytest.fixture
def svc(session, clock):
    return CmsService(session, clock=clock)


async def draft(svc, slug="pancakes", **kw):
    item = await svc.create(ORG, "recipe", slug, "ann")
    args = dict(title="Pancakes", body=BODY, created_by="ann") | kw
    await svc.save_version(ORG, item.id, "en", **args)
    return item


async def to_approved(svc, item):
    await svc.submit(ORG, item.id, ANN)
    await svc.approve(ORG, item.id, BO)


async def test_draft_to_published_with_checks_audit_and_results(svc, session):
    item = await draft(svc)
    await to_approved(svc, item)
    done = await svc.publish(ORG, item.id, BO)
    assert done.state == "published" and done.published_version_id == done.current_version_id
    assert (await svc.get_published(ORG, "pancakes")).title == "Pancakes"
    actions = [
        a.action for a in (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars()
    ]
    assert actions == ["cms.item.submit", "cms.item.approve", "cms.item.publish"]


async def test_missing_fields_block_publishing_and_leave_the_item_approved(svc):
    item = await svc.create(ORG, "recipe", "empty", "ann")
    await svc.save_version(ORG, item.id, "en", title="", body=[], created_by="ann")
    await to_approved(svc, item)
    with pytest.raises(PublishBlocked) as e:
        await svc.publish(ORG, item.id, BO)
    assert e.value.failed[0][0] == "required_fields" and e.value.failed[0][1].details[
        "missing"
    ] == ["title", "body"]
    assert (await svc.get(ORG, item.id)).state == "approved"
    assert await svc.get_published(ORG, "empty") is None


async def test_ai_written_versions_need_a_disclosure_block(svc):
    item = await draft(svc, "ai-pancakes", author_kind="ai")
    await to_approved(svc, item)
    with pytest.raises(PublishBlocked, match="disclosure_present"):
        await svc.publish(ORG, item.id, BO)
    await svc.save_version(
        ORG, item.id, "en", title="Pancakes", body=BODY, author_kind="ai", created_by="ann",
        disclosures=[await svc.disclosure(ORG, "ai_assistance", "en")],
    )  # fmt: skip
    assert (await svc.get(ORG, item.id)).state == "draft"  # new content needs a new review
    await to_approved(svc, item)
    assert (await svc.publish(ORG, item.id, BO)).state == "published"


async def test_disclosure_templates_are_per_org_and_language_with_fallbacks(svc):
    assert "AI" in (await svc.disclosure(ORG, "ai_assistance", "en"))["text"]  # built-in wording
    await svc.set_disclosure_template(ORG, "ai_assistance", "hi", "इस सामग्री में AI की मदद ली गई।")
    await svc.set_disclosure_template(ORG, "ai_assistance", "en", "Written with AI help.")
    assert (await svc.disclosure(ORG, "ai_assistance", "hi"))["text"].startswith("इस")
    assert (await svc.disclosure(ORG, "ai_assistance", "hi-IN"))["text"].startswith(
        "इस"
    )  # base language
    assert (await svc.disclosure(ORG, "ai_assistance", "ta"))[
        "text"
    ] == "Written with AI help."  # falls back to en
    assert (await svc.disclosure(OTHER, "ai_assistance", "en"))["text"].startswith(
        "Parts of this"
    )  # other org
    with pytest.raises(CmsError):
        await svc.disclosure(ORG, "gossip", "en")


async def test_all_variants_ready_blocks_until_each_language_is_marked_ready(session, clock):
    reg = default_registry()
    reg.register("recipe", AllVariantsReady(["en", "hi"]))
    svc = CmsService(session, checks=reg, clock=clock)
    item = await draft(svc)
    await to_approved(svc, item)
    await svc.mark_variant_ready(ORG, item.id, "en")
    with pytest.raises(PublishBlocked) as e:
        await svc.publish(ORG, item.id, BO)
    assert e.value.failed[0][1].details == {"not_ready": ["hi"]}
    # editing the approved English text sends it back to draft; add Hindi, ready both, go again
    await svc.save_version(ORG, item.id, "hi", title="पैनकेक", body=BODY, created_by="ann")
    await svc.mark_variant_ready(ORG, item.id, "en")
    await svc.mark_variant_ready(ORG, item.id, "hi")
    await to_approved(svc, item)
    await svc.publish(ORG, item.id, BO)
    assert (await svc.get_published(ORG, "pancakes", "hi")).title == "पैनकेक"
    assert (await svc.get_published(ORG, "pancakes", "fr")) is None
    assert {v.state for v in (await svc.variants(item.id)).values()} == {"published"}


async def test_non_blocking_failures_are_warnings(session, clock):
    class Picky:
        name, blocking = "has_summary", False

        async def run(self, ctx, item, version):
            return CheckResult(bool(version.summary), {})

    reg = default_registry()
    reg.register("*", Picky())
    svc = CmsService(session, checks=reg, clock=clock)
    item = await draft(svc)
    await to_approved(svc, item)
    await svc.publish(ORG, item.id, BO)
    meta = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "cms.item.publish")))
        .scalar_one()
        .meta
    )
    assert meta["warnings"] == ["has_summary"]
    with pytest.raises(ValueError, match="already registered"):
        reg.register("*", Picky())


async def test_review_rules(svc):
    item = await draft(svc)
    with pytest.raises(CmsError, match="needs in_review"):
        await svc.approve(ORG, item.id, BO)
    await svc.submit(ORG, item.id, ANN)
    with pytest.raises(CmsError, match="wrote it"):
        await svc.approve(ORG, item.id, Actor("ann", "reviewer"))  # four eyes
    assert (await svc.request_changes(ORG, item.id, BO, "needs a photo")).state == "draft"
    await svc.submit(ORG, item.id, ANN)
    await svc.approve(ORG, item.id, BO)
    with pytest.raises(CmsError, match="needs draft"):
        await svc.submit(ORG, item.id, ANN)
    empty = await svc.create(ORG, "recipe", "nothing", "ann")
    with pytest.raises(CmsError, match="save a version"):
        await svc.submit(ORG, empty.id, ANN)


async def test_slugs_are_unique_per_org_and_items_are_invisible_across_orgs(svc):
    item = await draft(svc)
    with pytest.raises(CmsError, match="already used"):
        await svc.create(ORG, "recipe", "pancakes")
    await svc.create(OTHER, "recipe", "pancakes")  # same slug, other org
    with pytest.raises(LookupError):
        await svc.get(OTHER, item.id)
    with pytest.raises(LookupError):
        await svc.publish(OTHER, item.id, BO)


async def test_scheduled_publish_waits_for_its_time_and_blocked_items_fall_back(session, db, clock):
    svc = CmsService(session, clock=clock)
    ok, bad = await draft(svc, "ok"), await draft(svc, "bad")
    await svc.save_version(ORG, bad.id, "en", title="Bad", body=[], created_by="ann")
    for i in (ok, bad):
        await to_approved(svc, i)
    at = clock.at + timedelta(hours=2)
    with pytest.raises(CmsError, match="timezone"):
        await svc.schedule(ORG, ok.id, at.replace(tzinfo=None), BO)
    await svc.schedule(ORG, ok.id, at, BO)
    await svc.schedule(ORG, bad.id, at, BO)
    await session.commit()

    reg = JobRegistry()
    register_jobs(reg, clock=clock)
    assert "cms-publish-due" in reg.schedules
    worker = Worker(reg, db, poll_interval=0.01)

    async def tick():
        async with db.session() as s:
            from tsp.jobs import enqueue

            await enqueue(s, "cms.publish_due")
        await worker.run_once()

    await tick()  # 09:00, nothing due yet
    async with db.session() as s:
        assert (await s.get(Item, ok.id)).state == "scheduled"
    clock.at = at
    await tick()
    async with db.session() as s:
        got_ok, got_bad = await s.get(Item, ok.id), await s.get(Item, bad.id)
        assert got_ok.state == "published" and got_ok.published_at == at
        assert (
            got_bad.state == "approved" and got_bad.publish_at is None
        )  # not retried every minute
        actions = {a.action for a in (await s.execute(select(AuditLog))).scalars()}
    assert {"cms.item.publish", "cms.item.publish_blocked"} <= actions


async def test_editing_after_approval_returns_to_draft_and_a_published_item_keeps_serving(svc):
    item = await draft(svc)
    await to_approved(svc, item)
    await svc.save_version(ORG, item.id, "en", title="Pancakes v2", body=BODY, created_by="ann")
    assert (await svc.get(ORG, item.id)).state == "draft"
    await to_approved(svc, item)
    await svc.publish(ORG, item.id, BO)
    with pytest.raises(CmsError, match="already published"):
        await svc.submit(ORG, item.id, ANN)
    await svc.save_version(ORG, item.id, "en", title="Pancakes v3", body=BODY, created_by="ann")
    live = await svc.get(ORG, item.id)
    assert (
        live.state == "published"
        and (await svc.get_published(ORG, "pancakes")).title == "Pancakes v2"
    )
    await svc.submit(
        ORG, item.id, ANN
    )  # a published item with pending changes goes back through review
    await svc.approve(ORG, item.id, BO)
    await svc.publish(ORG, item.id, BO)
    assert (await svc.get_published(ORG, "pancakes")).title == "Pancakes v3"


async def test_unpublish_needs_a_reason_and_hides_the_item(svc):
    item = await draft(svc)
    await to_approved(svc, item)
    await svc.publish(ORG, item.id, BO)
    with pytest.raises(CmsError, match="reason"):
        await svc.unpublish(ORG, item.id, " ", BO)
    out = await svc.unpublish(ORG, item.id, "factual error", BO)
    assert out.state == "unpublished" and await svc.get_published(ORG, "pancakes") is None
    with pytest.raises(CmsError, match="not published"):
        await svc.unpublish(ORG, item.id, "again", BO)


async def test_diff_between_versions(svc):
    item = await draft(svc)
    v1 = (await svc.versions(ORG, item.id))[0]
    v2 = await svc.save_version(
        ORG, item.id, "en", title="Fluffy Pancakes",
        body=[BODY[0], {"type": "paragraph", "text": "Fold in sifted flour."}, {"type": "paragraph", "text": "Rest 5 min."}],
        created_by="ann",
    )  # fmt: skip
    d = await svc.diff(ORG, v1.id, v2.id)
    assert d.title == ("Pancakes", "Fluffy Pancakes")
    assert [op for op, _, _ in d.ops] == ["replace"]
    assert d.ops[0][1] == [BODY[1]] and len(d.ops[0][2]) == 2
    same = await svc.diff(ORG, v2.id, v2.id)
    assert same.title is None and same.ops == []
    with pytest.raises(LookupError):
        await svc.diff(OTHER, v1.id, v2.id)


async def test_editor_api_enforces_roles_and_reports_blocked_publishes(db):
    app = FastAPI()
    who = {"p": Principal(ORG, "ann", frozenset({"editor"}))}

    async def sess():
        d = Database(URL)
        try:
            async with d.session() as s:
                yield s
        finally:
            await d.dispose()

    app.include_router(build_router(lambda: who["p"], sess), prefix="/api")
    c = TestClient(app)
    made = c.post("/api/cms/items", json={"type": "recipe", "slug": "waffles"})
    assert made.status_code == 201
    iid = made.json()["id"]
    assert c.post("/api/cms/items", json={"type": "recipe", "slug": "waffles"}).status_code == 409
    assert c.post("/api/cms/items", json={"type": "recipe", "slug": "Bad Slug"}).status_code == 422
    c.post(f"/api/cms/items/{iid}/versions", json={"lang": "en", "title": "", "body": []})
    assert c.post(f"/api/cms/items/{iid}/submit").json()["state"] == "in_review"
    assert c.post(f"/api/cms/items/{iid}/approve").status_code == 403  # editors cannot approve
    who["p"] = Principal(ORG, "bo", frozenset({"reviewer"}))
    assert c.post(f"/api/cms/items/{iid}/approve").json()["state"] == "approved"
    blocked = c.post(f"/api/cms/items/{iid}/publish")
    assert (
        blocked.status_code == 409
        and blocked.json()["detail"]["failed"][0]["check"] == "required_fields"
    )
    who["p"] = Principal(OTHER, "zed", frozenset({"editor", "reviewer"}))
    assert c.get(f"/api/cms/items/{iid}").status_code == 404
    who["p"] = Principal(ORG, "x", frozenset())
    assert c.get(f"/api/cms/items/{iid}").status_code == 403
    assert uuid.UUID(iid)
