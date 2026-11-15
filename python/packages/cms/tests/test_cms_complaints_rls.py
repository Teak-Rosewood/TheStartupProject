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
