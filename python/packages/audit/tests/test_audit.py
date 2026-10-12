import asyncio
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tsp.audit import Actor, client_ip, migrations_path, record
from tsp.db import Database, upgrade

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
APP_MIGRATIONS = None  # the audit branch alone needs no app migrations


@pytest.fixture
async def db(tmp_path):
    d = Database(URL)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    async with d.session() as s:
        await s.execute(text("DROP TABLE IF EXISTS audit_log, alembic_version CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    # an app migrations folder with no versions: only the module's branch applies
    (tmp_path / "versions").mkdir()
    (tmp_path / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (tmp_path / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, tmp_path, modules=["tsp.audit"])
    yield d
    async with d.session() as s:
        await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER USER"))
        await s.execute(text("DROP TABLE IF EXISTS audit_log, alembic_version CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    await d.dispose()


def req(xff=None, host="10.0.0.9", request_id="rid-1"):
    return SimpleNamespace(
        headers={"x-forwarded-for": xff} if xff else {},
        client=SimpleNamespace(host=host),
        state=SimpleNamespace(request_id=request_id),
    )


def test_migrations_path_exists():
    assert migrations_path().is_dir() and list(migrations_path().glob("audit_*.py"))


def test_client_ip_prefers_first_forwarded_address():
    assert client_ip(req("203.0.113.5, 130.176.0.1")) == "203.0.113.5"
    assert client_ip(req()) == "10.0.0.9"


async def test_record_stores_all_fields(db):
    async with db.session() as s:
        await record(
            s,
            "user.disabled",
            actor=Actor("admin-1", "support"),
            target=("user", "u-9"),
            request=req("198.51.100.7"),
            reason="abuse",
        )
    async with db.session() as s:
        row = (
            await s.execute(
                text(
                    "select actor_sub, actor_role, action, target_type, target_id, ip, request_id, meta from audit_log"
                )
            )
        ).one()
    assert tuple(row)[:7] == (
        "admin-1",
        "support",
        "user.disabled",
        "user",
        "u-9",
        "198.51.100.7",
        "rid-1",
    )
    assert row.meta == {"reason": "abuse"}


async def test_rows_cannot_be_changed_or_removed(db):
    async with db.session() as s:
        await record(s, "x", actor=Actor(None, "system"))
    for stmt in (
        "UPDATE audit_log SET action = 'y'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with db.session() as s:
                await s.execute(text(stmt))
    async with db.session() as s:
        assert (await s.execute(text("select count(*) from audit_log"))).scalar_one() == 1


async def test_record_rolls_back_with_the_caller(db):
    with pytest.raises(RuntimeError):
        async with db.session() as s:
            await record(s, "x", actor=Actor("a"))
            raise RuntimeError("boom")
    async with db.session() as s:
        assert (await s.execute(text("select count(*) from audit_log"))).scalar_one() == 0
