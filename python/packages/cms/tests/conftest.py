import asyncio

import pytest
from cms_support import URL, Clock, drop_all, ensure_database
from tsp.db import Database, upgrade


@pytest.fixture
async def db(tmp_path):
    try:
        await ensure_database()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    d = Database(URL, pool_size=10)
    await drop_all(d)
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.audit", "tsp.jobs", "tsp.flags", "tsp.cms"])
    yield d
    await drop_all(d)
    await d.dispose()


@pytest.fixture
async def session(db):
    async with db.session() as s:
        yield s


@pytest.fixture
def clock():
    return Clock()
