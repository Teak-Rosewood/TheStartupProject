import asyncio

import pytest
from distribute_support import URL
from sqlalchemy import text
from tsp.db import Database, upgrade

TABLES = "emails, email_suppressions, email_preferences, device_tokens, notifications, jobs, cron_jobs, alembic_version"


@pytest.fixture
async def db(tmp_path):
    d = Database(URL, pool_size=10)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    async with d.session() as s:
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.jobs", "tsp.notify"])
    yield d
    async with d.session() as s:
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
    await d.dispose()
