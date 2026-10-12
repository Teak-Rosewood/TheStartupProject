import os
from pathlib import Path

import pytest
from sqlalchemy import text
from tsp.db import Database, normalize_url, upgrade

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
FIXTURE = Path(__file__).parent / "migrations_fixture"


def test_normalize_url():
    assert normalize_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    assert normalize_url("postgresql+asyncpg://u@h/db") == "postgresql+asyncpg://u@h/db"


@pytest.fixture
async def db():
    d = Database(URL)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    yield d
    async with d.session() as s:
        await s.execute(text("DROP TABLE IF EXISTS tsp_test_items, alembic_version"))
    await d.dispose()


async def test_session_commits_and_rolls_back(db):
    async with db.session() as s:
        await s.execute(text("CREATE TABLE tsp_test_items (id int primary key, name text)"))
        await s.execute(text("INSERT INTO tsp_test_items VALUES (1, 'a')"))
    with pytest.raises(RuntimeError):
        async with db.session() as s:
            await s.execute(text("INSERT INTO tsp_test_items VALUES (2, 'b')"))
            raise RuntimeError("boom")
    async with db.session() as s:
        rows = (await s.execute(text("SELECT id FROM tsp_test_items"))).scalars().all()
    assert rows == [1]


async def test_migrate_upgrade_applies_and_is_idempotent(db):
    import asyncio

    await asyncio.to_thread(upgrade, URL, FIXTURE)
    await asyncio.to_thread(upgrade, URL, FIXTURE)  # second run is a no-op
    async with db.session() as s:
        n = (await s.execute(text("SELECT count(*) FROM tsp_test_items"))).scalar_one()
        v = (await s.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert n == 0 and v == "0001"


async def test_concurrent_migrations_are_serialized(db):
    """Separate processes, as in a real rolling deploy (alembic's context is not thread-safe)."""
    import asyncio
    import sys

    async def run():
        p = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tsp.db.cli",
            "migrate",
            "--url",
            URL,
            "--script-location",
            str(FIXTURE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await p.communicate()
        return p.returncode, err.decode()[-300:]

    results = await asyncio.gather(*(run() for _ in range(4)))
    assert [r[0] for r in results] == [0] * 4, results  # without the lock, create_table collides
    async with db.session() as s:
        v = (await s.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert v == "0001"
