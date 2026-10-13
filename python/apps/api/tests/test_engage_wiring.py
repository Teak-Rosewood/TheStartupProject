"""Reader engagement as the sample API mounts it."""

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
    "tsp.audit", "tsp.compliance", "tsp.accounts", "tsp.payments", "tsp.jobs", "tsp.notify",
    "tsp.growth", "tsp.flags", "tsp.storage", "tsp.metering", "tsp.cms", "tsp.pipeline", "tsp.engage",
]  # fmt: skip
ANN = "aaaaaaaa-0000-0000-0000-00000000000a"


async def _drop():
    d = Database(DB)
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_current")
        )
        await s.execute(
            text("ALTER TABLE IF EXISTS cms_items DROP CONSTRAINT IF EXISTS fk_cms_items_published")
        )
        await s.execute(text(f"DROP TABLE IF EXISTS {ALL_TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    await d.dispose()


@pytest.fixture
def client(monkeypatch):
    try:
        asyncio.run(_drop())
    except Exception:
        pytest.skip("local services not running (docker compose up -d)")
    asyncio.run(asyncio.to_thread(upgrade, DB, MIGRATIONS, modules=MODULES))
    import tsp_api.main as main

    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setattr(main, "build_llm", lambda: None)
    app = main.build_app()
    app.dependency_overrides[current_user] = lambda: Claims(
        sub=ANN,
        token_use="id",
        email="ann@example.com",
        raw={"email": "ann@example.com", "email_verified": True},
    )
    with TestClient(app) as c:
        yield c
    asyncio.run(_drop())


def test_onboarding_polls_and_private_history(client):
    r = client.get("/api/engage/me", params={"org_id": "pub"}).json()
    assert (
        r["points"] == 0
        and r["onboarding"]["remaining"] == ["welcome", "interests"]
        and r["polls"] == []
    )
    assert (
        client.post("/api/engage/onboarding/welcome", params={"org_id": "pub"}).json()["points"]
        == 10
    )
    assert (
        client.post("/api/engage/onboarding/welcome", params={"org_id": "pub"}).json()["points"]
        == 0
    )
    assert client.post("/api/engage/onboarding/skip", params={"org_id": "pub"}).status_code == 404
    assert client.get("/api/engage/me", params={"org_id": "pub"}).json()["points"] == 10


def test_pings_need_the_item_to_have_been_served(client):
    r = client.post(
        "/api/engage/receipts/pancakes/ping",
        json={"org_id": "pub", "dwell": 30, "scroll": 1, "reached_end": True},
    )
    assert r.status_code == 404


def test_submissions_need_an_ai_provider_for_moderation(client):
    assert (
        client.get("/api/engage/me/submissions").status_code == 404
    )  # not mounted without a model
    assert (
        client.get(
            "/api/engage/public/items/pancakes/submissions", params={"org_id": "pub"}
        ).status_code
        == 404
    )


def test_the_settings_parse(monkeypatch):
    from tsp_api.cms import cms_settings
    from tsp_api.engage import engage_config

    cfg = engage_config(
        {
            "ENGAGE_TIMEZONE": "UTC",
            "ENGAGE_WEEKLY_GOAL": "3",
            "ENGAGE_ONBOARDING_STEPS": "a, b",
            "ENGAGE_REQUIRE_SUBSCRIPTION": "0",
        }
    )
    assert (cfg.timezone, cfg.weekly_goal, cfg.required_onboarding, cfg.require_subscription) == (
        "UTC",
        3,
        ("a", "b"),
        False,
    )
    assert engage_config({}).require_subscription is True
    assert (
        "moderation_appeal" in cms_settings({}).complaints.categories
    )  # appeals have a category to land in
    assert cms_settings({"CMS_COMPLAINT_CATEGORIES": "x,y"}).complaints.categories == (
        "x",
        "y",
        "moderation_appeal",
    )
