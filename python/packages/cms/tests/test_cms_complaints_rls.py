"""Row-level security on the complaints desk binds an ordinary database role (superusers, which
the tests otherwise run as, bypass it)."""

from sqlalchemy import text
from tsp.cms import ComplaintsConfig, ComplaintsDesk
from tsp.db import scope_all_orgs, scope_org

ROLE = "tsp_rls_probe_cms"


async def as_probe(session) -> None:
    await session.execute(
        text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{ROLE}') "
            f"THEN CREATE ROLE {ROLE} NOLOGIN; END IF; END $$"
        )
    )
    await session.execute(text(f"GRANT SELECT ON cms_complaints, cms_complaint_events TO {ROLE}"))
    await session.execute(text(f"SET LOCAL ROLE {ROLE}"))


async def count(session, table: str) -> int:
    return (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


async def test_complaints_are_visible_only_to_their_organization(db, clock):
    async with db.session() as s:
        desk = ComplaintsDesk(s, config=ComplaintsConfig(), clock=clock)
        for org in ("org-a", "org-b"):
            await desk.intake(org, "Reader", "r@example.com", "other", "Something is wrong here.")

    async with db.session() as s:
        await as_probe(s)
        assert await count(s, "cms_complaints") == 0  # no scope set: nothing
        await scope_org(s, "org-a")
        assert await count(s, "cms_complaints") == 1
        assert await count(s, "cms_complaint_events") >= 1
        await scope_all_orgs(s)
        assert await count(s, "cms_complaints") == 2
        await s.rollback()

    async with db.session() as s:
        await as_probe(s)
        desk = ComplaintsDesk(s, config=ComplaintsConfig(), clock=clock)
        rows = await desk.listing("org-b")
        assert [c.org_id for c in rows] == ["org-b"]
        # Even a query that forgets its org filter sees only the scoped organization.
        assert await count(s, "cms_complaints") == 1
        await s.rollback()


async def test_the_desk_works_end_to_end_when_the_policy_binds(db, clock):
    """Intake, staff actions, the deadline sweep and the report all run as an ordinary role, so a
    missing scope call shows up here and not in production."""
    from datetime import timedelta

    from tsp.audit import Actor

    async with db.session() as s:
        await as_probe(s)
        await s.execute(text("RESET ROLE"))
        for kind in ("TABLES", "SEQUENCES"):
            await s.execute(text(f"GRANT ALL ON ALL {kind} IN SCHEMA public TO {ROLE}"))
        await s.execute(text(f"SET LOCAL ROLE {ROLE}"))
        desk = ComplaintsDesk(s, config=ComplaintsConfig(), clock=clock)
        staff = Actor("bo", "reviewer")
        a = await desk.intake(
            "org-a", "Reader", "r@example.com", "other", "Something is wrong here."
        )
        b = await desk.intake(
            "org-b", "Reader", "r@example.com", "other", "Something else is wrong."
        )
        await desk.acknowledge("org-a", a.id, staff)
        await desk.note("org-a", a.id, staff, "looking into it")
        assert "received" in {e.kind for e in await desk.events("org-a", a.id)}
        try:
            await desk.get("org-a", b.id)
            raise AssertionError("org-a must not reach org-b's complaint")
        except LookupError:
            pass
        clock.at += timedelta(days=60)
        swept = await desk.run_sla()
        assert set(swept["escalated"]) | set(swept["warned"]) >= {b.id}
        month = a.received_at.strftime("%Y-%m")
        assert (await desk.report("org-a", month))["total"] == 1
        await s.rollback()
