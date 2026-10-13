import asyncio

import pytest
from engage_support import TABLES, URL, Clock
from sqlalchemy import text
from tsp.db import Database, upgrade


@pytest.fixture
async def db(tmp_path):
    d = Database(URL, pool_size=10)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_current")
        )
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_published")
        )
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(
        upgrade,
        URL,
        app,
        modules=["tsp.audit", "tsp.jobs", "tsp.flags", "tsp.pipeline", "tsp.cms", "tsp.engage"],
    )
    yield d
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_current")
        )
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_published")
        )
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
    await d.dispose()


@pytest.fixture
async def session(db):
    async with db.session() as s:
        yield s


@pytest.fixture
def clock():
    return Clock()
