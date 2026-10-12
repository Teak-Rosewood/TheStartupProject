import asyncio
import os
from pathlib import Path

import pytest
from sqlalchemy import text
from tsp.db import Database, upgrade

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
LEGAL_SRC = Path(__file__).parents[4] / "docs" / "legal"


async def _drop(d: Database) -> None:
    async with d.session() as s:
        await s.execute(
            text("DROP TABLE IF EXISTS consents, data_requests, audit_log, alembic_version CASCADE")
        )
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))


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
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.audit", "tsp.compliance"])
    yield d
    async with d.session() as s:
        await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER USER"))
    await _drop(d)
    await d.dispose()
