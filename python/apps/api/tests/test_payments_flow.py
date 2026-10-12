import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path

import httpx
import pytest
from api_support import ALL_TABLES
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests_support import valid_gstin_for
from tsp.accounts import Accounts
from tsp.accounts import api as accounts_api
from tsp.auth import Claims
from tsp.db import Database, upgrade
from tsp.payments import Invoicer, LocalInvoiceStore, Payments, RazorpayProvider, Seller, load_plans
from tsp.payments import api as payments_api
from tsp.server import Settings, create_app, current_user, db_session

DB = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
MIGRATIONS = Path(__file__).parents[1] / "migrations"
MODULES = ["tsp.audit", "tsp.compliance", "tsp.accounts", "tsp.payments"]
TABLES = ALL_TABLES
OWNER, MEMBER = "aaaaaaaa-0000-0000-0000-00000000000a", "bbbbbbbb-0000-0000-0000-00000000000b"
WH = "whsec"


async def _drop():
    d = Database(DB)
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    await d.dispose()


def gateway(req: httpx.Request) -> httpx.Response:
    body = json.loads(req.content) if req.content else {}
    if req.url.path == "/v1/orders":
        return httpx.Response(
            200, json={"id": "order_1", "amount": body["amount"], "currency": body["currency"]}
        )
    return httpx.Response(404, json={"error": {"description": "unexpected"}})


@pytest.fixture
def client(tmp_path):
    try:
        asyncio.run(_drop())
    except Exception:
        pytest.skip("local services not running (docker compose up -d)")
    asyncio.run(asyncio.to_thread(upgrade, DB, MIGRATIONS, modules=MODULES))
    accounts = Accounts()
    api = accounts_api.build(accounts, current_user, db_session)
    provider = RazorpayProvider(
        "rzp", "secret", WH, http=httpx.AsyncClient(transport=httpx.MockTransport(gateway))
    )
    seller = Seller("Acme Pvt Ltd", valid_gstin_for("29"), "Bengaluru", "29", "998314")
    pay = Payments(
        {"razorpay": provider},
        default_provider="razorpay",
        invoicer=Invoicer(seller, LocalInvoiceStore(tmp_path / "inv")),
    )

    async def seed_plans():
        d = Database(DB)
        async with d.session() as s:
            await pay.sync_plans(s, load_plans(Path(__file__).parents[1] / "plans.toml"))
        await d.dispose()

    asyncio.run(seed_plans())
    app = create_app(Settings(database_url=DB))
    app.include_router(api.router, prefix="/api")
    app.include_router(
        payments_api.build_router(pay, api.account, api.org_member, db_session), prefix="/api"
    )
    who = {"sub": OWNER, "email": "owner@example.com"}
    app.dependency_overrides[current_user] = lambda: Claims(
        sub=who["sub"],
        token_use="id",
        email=who["email"],
        raw={"email": who["email"], "email_verified": True},
    )
    with TestClient(app) as c:
        c.who = who
        yield c
    asyncio.run(_drop())


def test_a_team_buys_credits_and_gets_an_invoice(client):
    org = client.post("/api/orgs", json={"name": "Acme"}).json()["id"]
    inv = client.post(f"/api/orgs/{org}/invites", json={"email": "member@example.com"}).json()
    client.who.update(sub=MEMBER, email="member@example.com")
    client.post("/api/invites/accept", json={"token": inv["token"]})
    # a plain member can read billing but cannot start a purchase
    assert client.get(f"/api/orgs/{org}/billing").status_code == 200
    assert (
        client.post(
            f"/api/orgs/{org}/billing/checkout", json={"plan_code": "credits_1000"}
        ).status_code
        == 403
    )

    client.who.update(sub=OWNER, email="owner@example.com")
    co = client.post(f"/api/orgs/{org}/billing/checkout", json={"plan_code": "credits_1000"})
    assert co.status_code == 201 and co.json()["checkout"]["order_id"] == "order_1"

    event = {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_1",
                    "order_id": "order_1",
                    "amount": 49900,
                    "currency": "INR",
                    "method": "upi",
                    "email": "owner@example.com",
                    "notes": {"org_id": org, "plan": "credits_1000"},
                }
            }
        },
    }
    body = json.dumps(event).encode()
    headers = {
        "X-Razorpay-Signature": hmac.new(WH.encode(), body, hashlib.sha256).hexdigest(),
        "X-Razorpay-Event-Id": "evt_1",
    }
    assert client.post("/api/webhooks/razorpay", content=body, headers=headers).json() == {
        "processed": 1,
        "duplicates": 0,
    }

    assert client.get(f"/api/orgs/{org}/billing").json()["credits"] == 1000
    (invoice,) = client.get(f"/api/orgs/{org}/invoices").json()
    assert invoice["total"] == 49900 and invoice["number"].startswith("INV/")
    # another organization cannot see this invoice or its PDF
    other = client.post("/api/orgs", json={"name": "Other"}).json()["id"]
    assert client.get(f"/api/orgs/{other}/invoices").json() == []
    assert client.get(f"/api/orgs/{other}/invoices/{invoice['id']}/pdf").status_code == 404
