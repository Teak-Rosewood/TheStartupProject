import asyncio
import os
import re
from pathlib import Path

import pytest
from api_support import ALL_TABLES
from fastapi.testclient import TestClient
from sqlalchemy import text
from tsp.auth import Claims
from tsp.db import Database, upgrade
from tsp.jobs import Worker
from tsp.notify import Notifier, Outgoing
from tsp.server import current_user
from tsp_api import main as app_main
from tsp_api.notifications import build_registry

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
]
TABLES = ALL_TABLES
ANN, BEA = "aaaaaaaa-0000-0000-0000-00000000000a", "bbbbbbbb-0000-0000-0000-00000000000b"


async def _drop():
    d = Database(DB)
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    await d.dispose()


class Capture:
    def __init__(self):
        self.sent: list[Outgoing] = []

    async def send(self, mail: Outgoing) -> str:
        self.sent.append(mail)
        return "mid"


@pytest.fixture
def client(monkeypatch, tmp_path):
    try:
        asyncio.run(_drop())
    except Exception:
        pytest.skip("local services not running (docker compose up -d)")
    asyncio.run(asyncio.to_thread(upgrade, DB, MIGRATIONS, modules=MODULES))
    capture = Capture()
    for k, v in {
        "DATABASE_URL": DB,
        "TSP_ENV": "dev",
        "APP_URL": "https://app.example.com",
        "TSP_JOBS_INLINE": "0",
        "TSP_LEGAL_MANIFEST": str(tmp_path / "none.json"),
    }.items():
        monkeypatch.setenv(k, v)
    real = app_main.build_notifier
    monkeypatch.setattr(app_main, "build_notifier", lambda: _with(real(), capture))
    app = app_main.build_app()
    who = {"sub": ANN, "email": "ann@example.com"}
    app.dependency_overrides[current_user] = lambda: Claims(
        sub=who["sub"],
        token_use="id",
        email=who["email"],
        raw={"email": who["email"], "email_verified": True, "name": who["sub"][:4]},
    )
    with TestClient(app) as c:
        c.who, c.capture, c.app_ = who, capture, app
        yield c
    asyncio.run(_drop())


def _with(notifier: Notifier, capture: Capture) -> Notifier:
    notifier.transport = capture
    return notifier


async def _deliver_all(app):
    from tsp.db import Database as D

    db = D(DB)
    registry = build_registry(
        app.state.notifier, compliance=app.state.compliance, payments=app.state.payments
    )
    n = await Worker(registry, db).run_once(limit=50)
    await db.dispose()
    return n


def test_first_sign_in_queues_a_welcome_email_and_the_worker_sends_it(client):
    client.get("/api/me")
    client.get("/api/me")  # only the first request creates the user
    asyncio.run(_deliver_all(client.app_))
    welcomes = [m for m in client.capture.sent if m.message.subject.startswith("Welcome")]
    assert len(welcomes) == 1 and welcomes[0].to == "ann@example.com"


def test_invites_are_emailed_with_a_one_time_link(client):
    org = client.post("/api/orgs", json={"name": "Acme"}).json()["id"]
    inv = client.post(f"/api/orgs/{org}/invites", json={"email": "bea@example.com"}).json()
    asyncio.run(_deliver_all(client.app_))
    mail = next(m for m in client.capture.sent if m.to == "bea@example.com")
    assert f"https://app.example.com/invite?token={inv['token']}" in mail.message.text
    assert "invited you to Acme" in mail.message.subject


def test_deletion_request_sends_a_confirmation_that_names_the_date(client):
    client.get("/api/me")
    assert client.post("/api/me/delete").status_code == 202
    asyncio.run(_deliver_all(client.app_))
    mail = next(m for m in client.capture.sent if "will be deleted on" in m.message.subject)
    assert mail.to == "ann@example.com"


def test_unsubscribe_flow_end_to_end(client):
    n = client.app_.state.notifier
    token = n.unsubscribe_token("fan@example.com")
    assert (
        "Unsubscribe" in client.get(f"/api/email/unsubscribe?token={token}").text
    )  # GET only asks
    assert client.get("/api/me/email-preferences").json()["marketing"] is False
    assert (
        client.put("/api/me/email-preferences", json={"marketing": True}).json()["marketing"]
        is True
    )
    assert client.get("/api/me/email-preferences").json()["marketing"] is True
    ann_token = n.unsubscribe_token("ann@example.com")
    assert client.post(f"/api/email/unsubscribe?token={ann_token}").status_code == 200
    assert client.get("/api/me/email-preferences").json()["marketing"] is False
    assert client.post("/api/email/unsubscribe?token=forged.token").status_code == 400


def test_devices_and_in_app_notifications(client):
    assert (
        client.put("/api/me/devices", json={"token": "x" * 20, "platform": "android"}).status_code
        == 204
    )
    assert (
        client.put("/api/me/devices", json={"token": "x" * 20, "platform": "palm"}).status_code
        == 422
    )

    async def seed():
        from tsp.notify import notify_user

        d = Database(DB)
        async with d.session() as s:
            await notify_user(s, ANN, "info", "Hello")
        await d.dispose()

    asyncio.run(seed())
    got = client.get("/api/me/notifications").json()
    assert got["unread"] == 1 and got["items"][0]["title"] == "Hello"
    assert client.post("/api/me/notifications/read", json={}).json() == {"marked": 1}
    assert client.get("/api/me/notifications?unread=true").json()["items"] == []


def test_housekeeping_and_schedules_are_registered(client):
    reg = build_registry(
        client.app_.state.notifier, compliance=client.app_.state.compliance, payments=None
    )
    assert {"compliance-deletions", "housekeeping"} <= set(reg.schedules)
    assert "payments.issue_missing_invoices" not in reg.handlers


def test_waitlist_double_opt_in_through_the_real_app(client):
    r = client.post(
        "/api/leads",
        json={
            "email": "fan@example.com",
            "consent": True,
            "source": "hero",
            "attribution": {"utm_source": "launch"},
        },
    )
    assert r.status_code == 202
    asyncio.run(_deliver_all(client.app_))
    mail = next(m for m in client.capture.sent if m.to == "fan@example.com")
    assert "confirm your email" in mail.message.subject.lower() or "Confirm" in mail.message.subject
    token = re.search(r"token=([A-Za-z0-9_-]+\.[0-9a-f]+)", mail.message.text).group(1)
    assert client.post(f"/api/leads/confirm?token={token}").json() == {"status": "confirmed"}

    async def consent():
        d = Database(DB)
        async with d.session() as s:
            n = client.app_.state.notifier
            ok = await n.marketing_allowed(s, "fan@example.com")
        await d.dispose()
        return ok

    assert asyncio.run(consent()) is True  # confirming recorded marketing consent


def test_events_and_referrals_through_the_real_app(client):
    batch = {
        "events": [
            {
                "name": "landing_viewed",
                "anonymous_id": "anon-12345678",
                "properties": {"email": "x@y.zz", "variant": "b"},
            }
        ]
    }
    assert client.post("/api/events", json=batch).json() == {"stored": 1}
    assert client.get("/api/me/referral").json()["referred"] == 0
    first = client.post("/api/me/attribution", json={"attribution": {"utm_source": "x"}}).json()
    assert first["attribution_saved"] is True


def test_new_modules_are_mounted_in_the_real_app(client):
    assert client.get("/api/flags").json() == {}
    org = client.post("/api/orgs", json={"name": "Acme"}).json()["id"]
    up = client.post(
        f"/api/orgs/{org}/files",
        json={"filename": "../a.png", "content_type": "image/png", "size": 100},
    )
    assert up.status_code == 201 and up.json()["upload"]["fields"]["key"].startswith(
        f"uploads/{org}/"
    )
    bad = client.post(
        f"/api/orgs/{org}/files",
        json={"filename": "x.exe", "content_type": "application/x-msdownload", "size": 100},
    )
    assert bad.status_code == 422 and bad.json()["error"]["type"] == "upload_rejected"
    assert client.get(f"/api/orgs/{org}/usage").json()["requests"] == 0
    assert client.app_.state.flags is not None and not hasattr(
        client.app_.state, "ai_gateway"
    )  # no model keys configured
