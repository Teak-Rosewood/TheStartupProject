import asyncio
import os

import pytest
from sqlalchemy import text
from tsp.db import Database, upgrade

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
CACHE = os.environ.get("TSP_TEST_CACHE_URL", "redis://127.0.0.1:56379/0")
TABLES = "leads, events, referral_codes, referrals, alembic_version"


async def _drop(d):
    async with d.session() as s:
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))


@pytest.fixture
async def db(tmp_path):
    d = Database(URL)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    await _drop(d)
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.growth"])
    yield d
    await _drop(d)
    await d.dispose()
