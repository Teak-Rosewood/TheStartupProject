"""The paywall as the sample API mounts it: off without a secret, on with one, and walling for real."""

import asyncio
import os
from pathlib import Path

import pytest
from api_support import ALL_TABLES
from fastapi.testclient import TestClient
from sqlalchemy import text
from tsp.auth import Claims
from tsp.db import Database, upgrade
from tsp.server import current_user

DB = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
MIGRATIONS = Path(__file__).parents[1] / "migrations"
MODULES = [
    "tsp.audit",
    "tsp.compliance",
    "tsp.accounts",
    "tsp.payments",
    "tsp.jobs",
    "tsp.notify",
    "tsp.growth",
    "tsp.flags",
    "tsp.storage",
    "tsp.metering",
    "tsp.paywall",
]


async def _drop():
    d = Database(DB)
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(f"DROP TABLE IF EXISTS {ALL_TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    await d.dispose()


def make(monkeypatch, secret):
    try:
        asyncio.run(_drop())
    except Exception:
        pytest.skip("local services not running (docker compose up -d)")
    asyncio.run(asyncio.to_thread(upgrade, DB, MIGRATIONS, modules=MODULES))
    import tsp_api.main as main

    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setattr(main, "build_llm", lambda: None)
    if secret:
        monkeypatch.setenv("PAYWALL_SECRET", secret)
    else:
        monkeypatch.delenv("PAYWALL_SECRET", raising=False)
    app = main.build_app()
    app.dependency_overrides[current_user] = lambda: Claims(
        sub="ann", token_use="id", email="a@example.com", raw={}
    )
    return app


def test_off_without_a_secret(monkeypatch):
    with TestClient(make(monkeypatch, None)) as c:
        assert c.get("/api/paywall/status", params={"org_id": "o", "item": "i"}).status_code == 404
    asyncio.run(_drop())


def test_on_with_a_secret_and_a_policy(monkeypatch):
    app = make(monkeypatch, "x" * 32)
    with TestClient(app) as c:

        async def policy():
            d = Database(DB)
            async with d.session() as s:
                await s.execute(
                    text(
                        "insert into pw_policies (id, org_id, name, free_items, window_days) values ('p', 'pub', 'w', 1, 7)"
                    )
                )
            await d.dispose()

        asyncio.run(policy())
        a = c.post("/api/paywall/read", json={"org_id": "pub", "item_id": "a"})
        assert a.json()["allowed"] is True and "pw_m" in c.cookies
        assert (
            c.post("/api/paywall/read", json={"org_id": "pub", "item_id": "b"}).json()["reason"]
            == "wall"
        )
    asyncio.run(_drop())
