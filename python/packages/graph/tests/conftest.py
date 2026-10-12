import asyncio

import pytest
from graph_support import TABLES, URL, _ensure_database
from sqlalchemy import text
from tsp.db import Database, upgrade


@pytest.fixture
async def session(tmp_path):
    try:
        await _ensure_database()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    db = Database(URL)
    async with db.session() as s:
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.graph"])
    async with db.session() as s:
        yield s
    async with db.session() as s:
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
    await db.dispose()
