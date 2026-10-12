import asyncio
import os

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from tsp.audit import Actor, AuditLog
from tsp.db import Database, upgrade
from tsp.flags import (
    Flag,
    FlagContext,
    Flags,
    FlagUpdate,
    bucket,
    build_router,
    require_enabled,
    require_not_killed,
)
from tsp.server import Settings, create_app, db_session

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
ADMIN = Actor("staff-1", "superadmin")


@pytest.fixture
async def db(tmp_path):
    d = Database(URL)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    drop = "DROP TABLE IF EXISTS flags, audit_log, alembic_version CASCADE"
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(drop))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.audit", "tsp.flags"])
    yield d
    async with d.session() as s:
        await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER USER"))
        await s.execute(text(drop))
    await d.dispose()


def flag(**kw) -> Flag:
    return Flag(
        key=kw.pop("key", "f"),
        enabled=kw.pop("enabled", True),
        rollout_percent=kw.pop("rollout_percent", 100),
        rules=kw.pop("rules", {}),
    )


def test_evaluation_rules():
    ev = Flags.evaluate
    ctx = FlagContext(user="u1", org="o1", plan="pro")
    assert (
        ev(None, ctx) is False and ev(None, ctx, default=True) is True
    )  # unknown flag: the caller's default
    assert ev(flag(enabled=False), ctx) is False  # master switch beats everything
    assert ev(flag(enabled=False, rules={"users": ["u1"]}), ctx) is False
    assert ev(flag(), ctx) is True
    assert ev(flag(rollout_percent=0), ctx) is False
    assert ev(flag(rollout_percent=0, rules={"users": ["u1"]}), ctx) is True
    assert ev(flag(rollout_percent=0, rules={"orgs": ["o1"]}), ctx) is True
    assert ev(flag(rollout_percent=0, rules={"plans": ["pro"]}), ctx) is True
    assert ev(flag(rollout_percent=0, rules={"plans": ["enterprise"]}), ctx) is False
    assert ev(flag(rules={"deny_orgs": ["o1"]}), ctx) is False  # deny beats a 100% rollout
    assert (
        ev(flag(rules={"deny_users": ["u1"], "users": ["u1"]}), ctx) is False
    )  # and beats an allow
    assert (
        ev(flag(rollout_percent=50), FlagContext()) is False
    )  # a partial rollout needs someone to bucket


def test_rollouts_are_stable_and_only_grow():
    people = [f"user-{i}" for i in range(2000)]
    at = {p: [bucket("new_checkout", p) for _ in range(2)] for p in people}
    assert all(a == b for a, b in at.values())  # the same person always lands in the same bucket
    ten = {p for p in people if bucket("new_checkout", p) < 10}
    thirty = {p for p in people if bucket("new_checkout", p) < 30}
    assert ten <= thirty  # raising 10% to 30% never removes anyone
    assert 140 < len(ten) < 260 and 520 < len(thirty) < 680  # close to 10% and 30% of 2000
    assert {
        p for p in people if bucket("other_flag", p) < 10
    } != ten  # different flags pick different people


async def test_set_audits_validates_and_takes_effect_at_once(db):
    flags = Flags(ttl=3600)
    async with db.session() as s:
        assert await flags.enabled(s, "beta") is False
        await flags.set(
            s, "beta", FlagUpdate(enabled=True), actor=ADMIN, reason="launch to everyone"
        )
        assert (
            await flags.enabled(s, "beta") is True
        )  # this process sees its own change immediately
        with pytest.raises(ValueError, match="flag keys"):
            await flags.set(s, "Bad Key!", FlagUpdate(enabled=True), actor=ADMIN, reason="x")
        with pytest.raises(ValueError, match="between 0 and 100"):
            await flags.set(s, "beta", FlagUpdate(rollout_percent=101), actor=ADMIN, reason="x")
    async with db.session() as s:
        row = (await s.execute(select(AuditLog))).scalar_one()
    assert (row.action, row.actor_sub, row.target_id, row.meta["reason"]) == (
        "flag.changed",
        "staff-1",
        "beta",
        "launch to everyone",
    )


async def test_cache_serves_reads_without_a_query_until_the_ttl_ends(db):
    clock = {"t": 0.0}
    flags = Flags(ttl=30, clock=lambda: clock["t"])
    async with db.session() as s:
        await flags.set(s, "x", FlagUpdate(enabled=True), actor=ADMIN, reason="r")
    other = Flags(ttl=30, clock=lambda: clock["t"])  # a second process
    async with db.session() as s:
        assert await other.enabled(s, "x") is True
        await s.execute(
            text("UPDATE flags SET enabled = false WHERE key = 'x'")
        )  # changed elsewhere
        assert await other.enabled(s, "x") is True  # still cached
        clock["t"] = 31
        assert await other.enabled(s, "x") is False  # TTL passed: sees the change


async def test_kill_switch_and_client_visible_flags(db):
    flags = Flags()
    async with db.session() as s:
        await flags.set(
            s, "kill:checkout", FlagUpdate(enabled=False), actor=ADMIN, reason="prepare"
        )
        assert await flags.killed(s, "checkout") is False
        await flags.set(
            s, "kill:checkout", FlagUpdate(enabled=True), actor=ADMIN, reason="gateway incident"
        )
        assert (
            await flags.killed(s, "checkout") is True and await flags.killed(s, "unknown") is False
        )
        await flags.set(
            s, "dark_mode", FlagUpdate(enabled=True, client_visible=True), actor=ADMIN, reason="r"
        )
        await flags.set(s, "secret_experiment", FlagUpdate(enabled=True), actor=ADMIN, reason="r")
        visible = await flags.client_flags(s, FlagContext(user="u1"))
    assert visible == {"dark_mode": True}  # kill switches and unmarked flags never reach browsers


async def test_http_helpers(db):
    flags = Flags(ttl=0)
    async with db.session() as s:
        await flags.set(s, "kill:search", FlagUpdate(enabled=True), actor=ADMIN, reason="r")

    from types import SimpleNamespace

    async def account():
        return SimpleNamespace(sub="u1")

    app = create_app(Settings(database_url=URL))
    app.include_router(build_router(flags, account, db_session), prefix="/api")

    @app.get("/api/search", dependencies=[Depends(require_not_killed(flags, "search", db_session))])
    async def search():
        return {"ok": True}

    @app.get(
        "/api/unreleased", dependencies=[Depends(require_enabled(flags, "unreleased", db_session))]
    )
    async def unreleased():
        return {"ok": True}

    with TestClient(app) as c:
        r = c.get("/api/search")
        assert r.status_code == 503 and r.json()["error"]["type"] == "feature_disabled"
        assert c.get("/api/unreleased").status_code == 404
        assert c.get("/api/flags").json() == {}
