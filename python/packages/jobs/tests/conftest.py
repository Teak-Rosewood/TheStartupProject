import asyncio
import os

import pytest
from sqlalchemy import text
from tsp.db import Database, upgrade

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")


async def _drop(d):
    async with d.session() as s:
        await s.execute(text("DROP TABLE IF EXISTS jobs, cron_jobs, alembic_version CASCADE"))


@pytest.fixture
async def db(tmp_path):
    d = Database(URL, pool_size=10)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    await _drop(d)
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.jobs"])
    yield d
    await _drop(d)
    await d.dispose()
