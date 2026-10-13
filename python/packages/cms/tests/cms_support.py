import os
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tsp.db import normalize_url

BASE_URL = os.environ.get(
    "TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test"
)
# A database of its own: other suites drop shared tables while they run.
URL = BASE_URL.rsplit("/", 1)[0] + "/tsp_cms_test"
TABLES = (
    "cms_complaint_events, cms_complaints, cms_windows, cms_corrections, cms_reviews, cms_disclosure_templates, cms_check_results, cms_variants, cms_items, cms_versions, "
    "flags, jobs, cron_jobs, audit_log, alembic_version"
)
ORG, OTHER = "org-1", "org-2"


class Clock:
    """A settable clock for deadlines and schedules."""

    def __init__(self, at=datetime(2026, 3, 1, 9, 0, tzinfo=UTC)):
        self.at = at

    def __call__(self):
        return self.at


async def ensure_database() -> None:
    engine = create_async_engine(normalize_url(BASE_URL), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as c:
            if not (
                await c.execute(text("SELECT 1 FROM pg_database WHERE datname = 'tsp_cms_test'"))
            ).first():
                await c.execute(text("CREATE DATABASE tsp_cms_test TEMPLATE template0"))
    finally:
        await engine.dispose()


async def drop_all(db) -> None:
    async with db.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_current")
        )
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_published")
        )
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
