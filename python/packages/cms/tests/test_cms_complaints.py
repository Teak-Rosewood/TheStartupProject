"""K4-C: the complaints desk (deadlines and escalation with a frozen clock), restricted-mode windows,
and export and erasure of complainant data."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cms_support import ORG, OTHER
from sqlalchemy import select, text
from tsp.audit import Actor, AuditLog
from tsp.cms import (
    CmsError,
    CmsService,
    Complaint,
    ComplaintEvent,
    ComplaintsConfig,
    ComplaintsDesk,
    Correction,
    Level,
    Principal,
    WindowRules,
    Windows,
    build_complaints_router,
    build_intake_router,
    default_registry,
    flag_key,
    register_complaints_with_compliance,
    register_jobs,
    sync_window_flags,
)
from tsp.jobs import JobRegistry
from tsp.server import Settings, create_app, db_session

ANN, BO = Actor("ann", "editor"), Actor("bo", "reviewer")
BODY = [{"type": "paragraph", "text": "Whisk the eggs."}]
CFG = ComplaintsConfig(
    ack_hours=48,
    warn_ack_hours=12,
    warn_resolve_days=3,
    levels=(Level("desk", 10), Level("chief", 5), Level("ombudsman", 5)),
)


class Mail:
    def __init__(self):
        self.sent = []

    async def queue(self, session, to, template, ctx, *, category="transactional"):
        self.sent.append((to, template, ctx))
        return "id"


@pytest.fixture
def mail():
    return Mail()


@pytest.fixture
def staff():
    return []


@pytest.fixture
def desk(session, clock, mail, staff):
    async def notify_staff(s, c, kind):
        staff.append((c.reference, kind))

    return ComplaintsDesk(session, config=CFG, clock=clock, mailer=mail, notify_staff=notify_staff)


async def make_complaint(desk, **kw):
    args = (
        dict(
            name="Reader One",
            email="Reader@Example.com",
            category="accuracy",
            body="The oven temperature is wrong.",
        )
        | kw
    )
    return await desk.intake(
        ORG, args.pop("name"), args.pop("email"), args.pop("category"), args.pop("body"), **args
    )


async def published_item(session, clock, slug="pancakes"):
    svc = CmsService(session, clock=clock)
    item = await svc.create(ORG, "recipe", slug, "ann")
    await svc.save_version(ORG, item.id, "en", title="Pancakes", body=BODY, created_by="ann")
    await svc.submit(ORG, item.id, ANN)
    await svc.approve(ORG, item.id, BO)
    return await svc.publish(ORG, item.id, BO)


# -- intake -------------------------------------------------------------------------------------


async def test_intake_sets_deadlines_sends_a_reference_and_validates(desk, mail, staff, clock):
    c = await make_complaint(desk)
    assert c.reference == f"C-2026-{c.seq:06d}" and c.status == "received" and c.level == 1
    assert c.email == "reader@example.com" and c.assigned_to == "desk"
    assert c.ack_due_at == clock() + timedelta(
        hours=48
    ) and c.resolve_due_at == clock() + timedelta(days=10)
    ((to, template, ctx),) = mail.sent
    assert (to, template, ctx["reference"]) == (
        "reader@example.com",
        "complaint_received",
        c.reference,
    ) and ctx["ack_hours"] == 48
    assert staff == [(c.reference, "received")]
    for bad in (
        dict(name=" "),
        dict(email="nope"),
        dict(category="rude"),
        dict(body="short"),
        dict(item_slug="missing"),
    ):
        with pytest.raises(CmsError):
            await make_complaint(desk, **bad)


async def test_a_complaint_about_a_page_is_linked_to_it(desk, session, clock):
    item = await published_item(session, clock)
    c = await make_complaint(desk, item_slug="pancakes")
    assert c.item_id == item.id


# -- deadlines and escalation (frozen clock) -------------------------------------------------------


async def test_warns_before_the_acknowledgement_deadline_then_escalates_after_it(
    desk, clock, staff
):
    c = await make_complaint(desk)
    clock.at += timedelta(hours=30)
    assert await desk.run_sla() == {"warned": [], "escalated": []}
    clock.at += timedelta(hours=8)  # 38h: within 12h of the 48h deadline
    assert (await desk.run_sla())["warned"] == [c.id]
    assert await desk.run_sla() == {"warned": [], "escalated": []}  # warned once only
    assert (c.reference, "warn_ack") in staff
    clock.at += timedelta(hours=11)  # 49h: missed
    assert (await desk.run_sla())["escalated"] == [c.id]
    assert (c.level, c.status, c.assigned_to) == (2, "escalated", "chief")
    assert c.resolve_due_at == clock() + timedelta(days=5) and c.ack_due_at == clock() + timedelta(
        hours=48
    )
    assert staff[-1] == (c.reference, "escalated")
    kinds = [e.kind for e in await desk.events(ORG, c.id)]
    assert kinds == ["received", "warning", "escalated"]


async def test_an_acknowledged_complaint_is_only_judged_on_the_resolution_deadline(desk, clock):
    c = await make_complaint(desk)
    await desk.acknowledge(ORG, c.id, ANN, "We are looking into it.")
    assert (c.status, c.ack_due_at) == ("acknowledged", None)
    clock.at += timedelta(days=8)  # inside the last 3 days of 10
    assert (await desk.run_sla())["warned"] == [c.id]
    clock.at += timedelta(days=3)  # day 11: missed
    assert (await desk.run_sla())["escalated"] == [c.id]
    assert c.level == 2 and c.ack_due_at is None  # already acknowledged: no new ack deadline
    clock.at += timedelta(days=6)
    assert (
        (await desk.run_sla())["escalated"] == [c.id]
        and c.level == 3
        and c.assigned_to == "ombudsman"
    )


async def test_at_the_last_level_a_breach_is_flagged_not_escalated_further(desk, clock, staff):
    c = await make_complaint(desk)
    await desk.acknowledge(ORG, c.id, ANN)
    await desk.escalate(ORG, c.id, BO, "manual")
    await desk.escalate(ORG, c.id, BO, "manual")
    assert c.level == 3
    clock.at += timedelta(days=6)
    assert await desk.run_sla() == {"warned": [], "escalated": []}
    assert (c.reference, "breach") in staff and c.resolve_due_at == clock() + timedelta(days=7)
    assert await desk.run_sla() == {
        "warned": [],
        "escalated": [],
    }  # looked at again in a week, not hourly
    assert [e.kind for e in await desk.events(ORG, c.id)].count("breach") == 1


async def test_closed_complaints_are_ignored_by_the_job(desk, clock):
    c = await make_complaint(desk)
    await desk.reject(ORG, c.id, BO, "Not about our content.")
    clock.at += timedelta(days=60)
    assert await desk.run_sla() == {"warned": [], "escalated": []}
    with pytest.raises(CmsError, match="already rejected"):
        await desk.resolve(ORG, c.id, BO, "x")


# -- resolving, with a correction ------------------------------------------------------------------------


async def test_resolving_with_a_correction_links_the_public_log_entry(desk, session, clock, mail):
    await published_item(session, clock)
    c = await make_complaint(desk, item_slug="pancakes")
    with pytest.raises(CmsError, match="say what was decided"):
        await desk.resolve(ORG, c.id, BO, " ")
    done = await desk.resolve(
        ORG,
        c.id,
        BO,
        "You were right. We fixed the temperature.",
        correction=("correction", "Oven temperature corrected to 200C."),
    )
    assert (
        done.status == "resolved"
        and done.closed_at == clock()
        and (done.ack_due_at, done.resolve_due_at) == (None, None)
    )
    entry = await session.get(Correction, done.correction_id)
    assert (
        entry.complaint_id == c.id
        and entry.public_note.startswith("Oven temperature")
        and entry.internal_note == f"complaint {c.reference}"
    )
    assert "complaint" not in str(
        await CmsService(session, clock=clock).corrections(ORG, "pancakes")
    )  # internal note stays internal
    assert mail.sent[-1][1] == "complaint_update" and mail.sent[-1][2]["final"] is True
    loose = await make_complaint(desk)
    with pytest.raises(CmsError, match="not about a page"):
        await desk.resolve(ORG, loose.id, BO, "ok", correction=("correction", "x"))
    actions = [a.action for a in (await session.execute(select(AuditLog))).scalars()]
    assert "cms.complaint.resolve" in actions and "cms.complaint.received" in actions


async def test_other_organizations_cannot_see_a_complaint(desk):
    c = await make_complaint(desk)
    with pytest.raises(LookupError):
        await desk.get(OTHER, c.id)
    assert await desk.listing(OTHER) == []


# -- the monthly report --------------------------------------------------------------------------------


async def test_monthly_report_and_csv(desk, clock):
    a = await make_complaint(desk)
    await desk.acknowledge(ORG, a.id, ANN)
    clock.at += timedelta(hours=6)
    b = await make_complaint(desk, category="privacy")
    clock.at += timedelta(hours=42)
    await desk.resolve(ORG, a.id, BO, "Fixed.")
    clock.at += timedelta(days=40)
    c = await make_complaint(desk, category="privacy")  # in April, not March
    rep = await desk.report(ORG, "2026-03")
    assert rep["total"] == 2 and rep["by_category"] == {"accuracy": 1, "privacy": 1}
    assert (
        rep["by_status"] == {"received": 1, "resolved": 1} and rep["median_days_to_resolve"] == 2.0
    )
    assert rep["median_hours_to_acknowledge"] == 0.0 and rep["escalated"] == 0
    assert (await desk.report(ORG, "2026-04"))["total"] == 1 and (
        await desk.report(ORG, "2025-01")
    )["total"] == 0
    with pytest.raises(CmsError, match="month"):
        await desk.report(ORG, "March")
    csv = await desk.report_csv(ORG, "2026-03")
    assert (
        csv.splitlines()[0].startswith("reference,")
        and a.reference in csv
        and b.reference in csv
        and c.reference not in csv
    )
    assert (
        "example.com" not in csv and "Reader" not in csv
    )  # no personal data in the shareable export


# -- restricted-mode windows ---------------------------------------------------------------------------------


def at(day, hour=0):
    return datetime(2026, 3, day, hour, tzinfo=UTC)


async def test_a_window_blocks_a_type_only_during_its_dates_and_region(session, clock):
    reg = default_registry()
    reg.register("*", WindowRules(clock))
    svc = CmsService(session, clock=clock, checks=reg)
    wins = Windows(session, clock=clock)
    await wins.create(
        ORG,
        "state-election",
        at(10),
        at(12),
        region="IN-KA",
        rules={"block_types": ["poll_results"]},
    )
    assert [w.name for w in await wins.active(ORG, at(11), "IN-KA")] == ["state-election"]
    assert await wins.active(ORG, at(11), "IN-MH") == [] and await wins.active(ORG, at(11)) == []
    assert (
        await wins.active(ORG, at(9)) == [] and await wins.active(ORG, at(12)) == []
    )  # [start, end)

    async def attempt(slug, type_, **attrs):
        item = await svc.create(ORG, type_, slug, "ann", attrs=attrs)
        await svc.save_version(ORG, item.id, "en", title=slug, body=BODY, created_by="ann")
        await svc.submit(ORG, item.id, ANN)
        await svc.approve(ORG, item.id, BO)
        return await svc.publish(ORG, item.id, BO), item

    clock.at = at(9)
    done, _ = await attempt("before", "poll_results", region="IN-KA")
    assert done.state == "published"
    clock.at = at(11)
    from tsp.cms import PublishBlocked

    with pytest.raises(PublishBlocked) as blocked:
        await attempt("during", "poll_results", region="IN-KA")
    assert blocked.value.failed[0][1].details["blocked_by"] == ["state-election"]
    ok, _ = await attempt("other-type", "recipe", region="IN-KA")  # other types are fine
    ok2, _ = await attempt("other-region", "poll_results", region="IN-MH")  # other regions are fine
    assert ok.state == ok2.state == "published"
    clock.at = at(13)
    after, _ = await attempt("after", "poll_results", region="IN-KA")
    assert after.state == "published"


async def test_required_attestations_during_a_window(session, clock):
    reg = default_registry()
    reg.register("*", WindowRules(clock))
    svc = CmsService(session, clock=clock, checks=reg)
    await Windows(session, clock=clock).create(
        ORG, "case", at(1), at(31), rules={"require_check": ["legal_review"]}
    )
    item = await svc.create(ORG, "recipe", "x", "ann")
    await svc.save_version(ORG, item.id, "en", title="x", body=BODY, created_by="ann")
    await svc.submit(ORG, item.id, ANN)
    await svc.approve(ORG, item.id, BO)
    from tsp.cms import PublishBlocked

    with pytest.raises(PublishBlocked) as e:
        await svc.publish(ORG, item.id, BO)
    assert e.value.failed[0][1].details["missing_attestations"] == ["legal_review"]
    item.attrs = {"attested": ["legal_review"]}
    assert (await svc.publish(ORG, item.id, BO)).state == "published"


async def test_window_validation(session, clock):
    w = Windows(session, clock=clock)
    with pytest.raises(ValueError, match="end after"):
        await w.create(ORG, "bad", at(5), at(5))
    with pytest.raises(ValueError, match="timezone"):
        await w.create(ORG, "bad", datetime(2026, 3, 1), at(5))


async def test_windows_set_flags_so_other_modules_can_react(db, clock):
    from tsp.flags import Flags

    flags = Flags(ttl=0)
    async with db.session() as s:
        await Windows(s, clock=clock).create(ORG, "State Election", at(10), at(12))
    clock.at = at(9)
    async with db.session() as s:
        assert await sync_window_flags(s, flags, clock=clock) == [
            flag_key("State Election")
        ]  # created, off
        assert not await flags.enabled(s, "cms.window.state-election")
    clock.at = at(11)
    async with db.session() as s:
        assert await sync_window_flags(s, flags, clock=clock) == ["cms.window.state-election"]
        assert (
            await sync_window_flags(s, flags, clock=clock) == []
        )  # nothing changed, nothing written
        assert await flags.enabled(s, "cms.window.state-election")
    clock.at = at(13)
    async with db.session() as s:
        await sync_window_flags(s, flags, clock=clock)
        assert not await flags.enabled(s, "cms.window.state-election")
        await Windows(s).delete(ORG, "State Election")
    async with db.session() as s:
        await s.execute(
            text("update flags set enabled = true where key = 'cms.window.state-election'")
        )
    async with db.session() as s:
        assert await sync_window_flags(s, flags, clock=clock) == [
            "cms.window.state-election"
        ]  # orphan switched off


# -- personal data ------------------------------------------------------------------------------------------


async def test_complainant_data_is_exported_and_erased_but_the_facts_remain(desk, session, clock):
    mine = await make_complaint(
        desk, email="me@example.com", body="My address is 12 Rose Lane and the recipe is wrong."
    )
    await desk.note(ORG, mine.id, ANN, "Called me@example.com back")
    await desk.resolve(ORG, mine.id, BO, "Corrected.")
    other = await make_complaint(desk, email="other@example.com")

    class Comp:
        def __init__(self):
            self.ex, self.er = {}, {}

        def register_exporter(self, n, f):
            self.ex[n] = f

        def register_eraser(self, n, f):
            self.er[n] = f

    async def email_of(s, sub):
        return {"u1": "ME@example.com"}.get(sub)

    comp = Comp()
    register_complaints_with_compliance(comp, email_of)
    out = await comp.ex["complaints"](session, "u1")
    assert (
        [r["reference"] for r in out] == [mine.reference]
        and "12 Rose Lane" in out[0]["message"]
        and out[0]["resolution"] == "Corrected."
    )
    assert await comp.ex["complaints"](session, "stranger") == []
    await comp.er["complaints"](session, "u1")
    await session.refresh(mine)
    await session.refresh(other)
    assert (mine.complainant_name, mine.body) == (
        "[removed]",
        "[removed]",
    ) and mine.email.startswith("deleted:")
    assert (mine.category, mine.status, mine.level, mine.resolution) == (
        "accuracy",
        "resolved",
        1,
        "Corrected.",
    )
    assert other.email == "other@example.com"
    notes = [
        e.note
        for e in (
            await session.execute(
                select(ComplaintEvent).where(ComplaintEvent.complaint_id == mine.id)
            )
        ).scalars()
        if e.kind == "note"
    ]
    assert notes == ["[removed]"]


# -- jobs ----------------------------------------------------------------------------------------------------------


async def test_the_hourly_and_minutely_jobs_are_registered_when_their_parts_are_given(db):
    reg = JobRegistry()
    register_jobs(reg)
    assert set(reg.schedules) == {"cms-publish-due", "cms-lifecycle"}
    reg = JobRegistry()
    register_jobs(reg, desk=lambda s: ComplaintsDesk(s), flags=object())
    assert reg.schedules["cms-complaints-sla"][0] == "every 1h"
    assert {"cms-complaints-sla", "cms-windows-sync"} <= set(reg.schedules)
    assert "cms.complaints_sla" in reg.handlers and "cms.windows_sync" in reg.handlers


async def test_the_sla_job_escalates_through_the_worker(db, clock):
    from tsp.jobs import Worker, enqueue

    async with db.session() as s:
        c = await make_complaint(ComplaintsDesk(s, config=CFG, clock=clock))
        cid = c.id
    clock.at += timedelta(hours=50)
    reg = JobRegistry()
    register_jobs(reg, desk=lambda s: ComplaintsDesk(s, config=CFG, clock=clock))
    async with db.session() as s:
        await enqueue(s, "cms.complaints_sla")
    assert await Worker(reg, db, poll_interval=0.05).run_once() == 1
    async with db.session() as s:
        assert (await s.get(Complaint, cid)).level == 2


# -- HTTP ----------------------------------------------------------------------------------------------------------


class Cache:
    def __init__(self):
        self.hits = {}

    async def allow(self, key, limit, window):
        self.hits[key] = self.hits.get(key, 0) + 1
        return self.hits[key] <= limit


async def test_public_intake_is_rate_limited_checked_and_returns_only_a_reference(db, clock, mail):
    cache = Cache()
    seen = []

    async def captcha(token, ip):
        seen.append(token)
        return token == "good"

    app = create_app(Settings())
    app.state.db = db
    factory = lambda s: ComplaintsDesk(s, config=CFG, clock=clock, mailer=mail)  # noqa: E731
    app.include_router(
        build_intake_router(db_session, ORG, factory, lambda: cache, captcha=captcha, per_hour=3)
    )
    body = {
        "name": "Reader",
        "email": "r@example.com",
        "category": "accuracy",
        "body": "The page says 5 eggs, the video says 3.",
        "captcha_token": "good",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://t",
        headers={"x-forwarded-for": "198.51.100.4"},
    ) as c:
        ok = await c.post("/public/cms/complaints", json=body)
        assert (
            ok.status_code == 201
            and list(ok.json()) == ["reference"]
            and ok.json()["reference"].startswith("C-2026-")
        )
        assert (
            await c.post("/public/cms/complaints", json={**body, "captcha_token": "bad"})
        ).status_code == 400
        assert (
            await c.post("/public/cms/complaints", json={**body, "category": "rude"})
        ).status_code == 422
        bot = await c.post("/public/cms/complaints", json={**body, "website": "http://spam"})
        assert bot.status_code == 201 and bot.json()["reference"] == "C-0000-000000"
        assert (
            await c.post("/public/cms/complaints", json=body)
        ).status_code == 429  # 4th request from this address
    async with db.session() as s:
        assert (
            await s.execute(select(text("count(*)")).select_from(Complaint))
        ).scalar_one() == 1  # bot and rejected ones left no record
    assert len(mail.sent) == 1


async def test_staff_routes_enforce_roles(db, clock, mail):
    who = {"p": Principal(ORG, "ann", frozenset({"editor"}))}

    async def principal():
        return who["p"]

    app = create_app(Settings())
    app.state.db = db
    factory = lambda s: ComplaintsDesk(s, config=CFG, clock=clock, mailer=mail)  # noqa: E731
    app.include_router(build_complaints_router(principal, db_session, factory), prefix="/api")
    async with db.session() as s:
        c = await make_complaint(ComplaintsDesk(s, config=CFG, clock=clock, mailer=mail))
        cid = str(c.id)
    base = f"/api/cms/complaints/{cid}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as h:
        assert (await h.get("/api/cms/complaints?status=open")).json()[0]["id"] == cid
        detail = (await h.get(base)).json()
        assert detail["email"] == "reader@example.com" and detail["events"][0]["kind"] == "received"
        assert (await h.post(f"{base}/acknowledge", json={"text": "Looking into it."})).json()[
            "status"
        ] == "acknowledged"
        assert (await h.post(f"{base}/notes", json={"text": "called"})).status_code == 201
        assert (
            await h.post(f"{base}/resolve", json={"resolution": "Done"})
        ).status_code == 403  # editors cannot close
        assert (await h.post(f"{base}/escalate", json={"text": "x"})).status_code == 403
        who["p"] = Principal(ORG, "bo", frozenset({"reviewer"}))
        assert (await h.post(f"{base}/escalate", json={"text": "needs the chief"})).json()[
            "level"
        ] == 2
        assert (await h.post(f"{base}/resolve", json={"resolution": "Done"})).json()[
            "status"
        ] == "resolved"
        assert (await h.post(f"{base}/resolve", json={"resolution": "Again"})).status_code == 409
        rep = (await h.get("/api/cms/complaints/report?month=2026-03")).json()
        assert rep["total"] == 1
        csv = await h.get("/api/cms/complaints/report.csv?month=2026-03")
        assert (
            csv.headers["content-type"].startswith("text/csv")
            and "attachment" in csv.headers["content-disposition"]
        )
        assert (await h.get(f"/api/cms/complaints/{uuid.uuid4()}")).status_code == 404
        who["p"] = Principal(OTHER, "zed", frozenset({"reviewer"}))
        assert (await h.get(base)).status_code == 404  # another organization
        who["p"] = Principal(ORG, "nobody", frozenset())
        assert (await h.get("/api/cms/complaints")).status_code == 403


async def test_the_migration_rolls_back_cleanly(db):
    async with db.session() as s:
        names = set(
            (
                await s.execute(
                    text(
                        "select table_name from information_schema.tables where table_name like 'cms_%'"
                    )
                )
            ).scalars()
        )
    assert {"cms_complaints", "cms_complaint_events", "cms_windows"} <= names
