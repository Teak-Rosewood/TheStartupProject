import asyncio

import pytest
from pipeline_support import URL, Harness, _drop, _ensure_database
from tsp.db import Database, upgrade
from tsp.flags import Flags


@pytest.fixture
async def db(tmp_path):
    try:
        await _ensure_database()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    d = Database(URL, pool_size=10)
    await _drop(d)
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(
        upgrade, URL, app, modules=["tsp.audit", "tsp.flags", "tsp.jobs", "tsp.pipeline"]
    )
    yield d
    await _drop(d)
    await d.dispose()


@pytest.fixture
def make(db):
    return lambda workflows, **kw: Harness(db, workflows, **kw)


@pytest.fixture
def flags():
    return Flags(ttl=0)
