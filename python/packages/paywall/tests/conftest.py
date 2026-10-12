import asyncio

import pytest
from paywall_support import SECRET, TABLES, URL, MemCache, NoDns
from sqlalchemy import text
from tsp.db import Database, upgrade
from tsp.paywall import Paywall


@pytest.fixture
async def db(tmp_path):
    d = Database(URL)
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
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.paywall"])
    yield d
    async with d.session() as s:
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
    await d.dispose()


@pytest.fixture
def pw():
    return Paywall(SECRET, MemCache(), resolver=NoDns(), shared_after=3)
