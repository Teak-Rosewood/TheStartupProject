import asyncio
import hashlib
import hmac
import json
import os

import httpx
import pytest
from sqlalchemy import text
from tsp.db import Database, upgrade
from tsp.payments import Invoicer, Payments, PlanSpec, RazorpayProvider, Seller

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
TABLES = (
    "credit_ledger, credit_accounts, invoices, invoice_sequences, webhook_events, transfers, refunds, payments, "
    "subscriptions, payment_customers, plans, billing_profiles, audit_log, alembic_version"
)
WH = "whsec_test"
_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def make_gstin(state: str, pan: str = "AAAPL1234C") -> str:
    body = f"{state}{pan}1Z"
    total = 0
    for i, ch in enumerate(body):
        v = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return body + _CHARS[(36 - total % 36) % 36]


SELLER_GSTIN = make_gstin("29")  # Karnataka
PLANS = [
    PlanSpec(
        "pro_monthly",
        "Pro (monthly)",
        "subscription",
        11800,
        interval="month",
        provider_plan_id="plan_pro",
        features={"exports": True, "seats": 10},
    ),
    PlanSpec("credits_100", "100 credits", "credit_pack", 11800, credits=100),
]


class LocalStore:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.fail = False

    async def put(self, key: str, data: bytes) -> None:
        if self.fail:
            raise RuntimeError("storage down")
        self.files[key] = data

    async def url(self, key: str, ttl_seconds: int) -> str:
        return f"local://{key}"


async def _drop(d: Database) -> None:
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
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
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.audit", "tsp.payments"])
    yield d
    await _drop(d)
    await d.dispose()


class Gateway:
    """Fake Razorpay HTTP API that records calls and hands out sequential ids."""

    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else {}
        self.calls.append((req.method, req.url.path, body))
        self.n += 1
        p = req.url.path
        if p == "/v1/customers":
            return httpx.Response(200, json={"id": f"cust_{self.n}"})
        if p == "/v1/orders":
            return httpx.Response(
                200,
                json={
                    "id": f"order_{self.n}",
                    "amount": body["amount"],
                    "currency": body["currency"],
                },
            )
        if p == "/v1/subscriptions":
            return httpx.Response(200, json={"id": f"sub_{self.n}", "status": "created"})
        if p.endswith("/cancel"):
            return httpx.Response(200, json={"status": "active"})
        if p.endswith("/refund"):
            return httpx.Response(
                200,
                json={
                    "id": f"rfnd_{self.n}",
                    "amount": body.get("amount", 0),
                    "status": "processed",
                },
            )
        if p.endswith("/transfers"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": f"trf_{self.n}",
                            "amount": body["transfers"][0]["amount"],
                            "status": "created",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"error": {"description": "not found"}})


@pytest.fixture
def gateway():
    return Gateway()


@pytest.fixture
def store():
    return LocalStore()


@pytest.fixture
def pay(gateway, store):
    http = httpx.AsyncClient(transport=httpx.MockTransport(gateway))
    prov = RazorpayProvider("rzp_test", "secret", WH, http=http)
    seller = Seller(
        "Acme Technologies Pvt Ltd", SELLER_GSTIN, "1 MG Road, Bengaluru", "29", "998314"
    )
    return Payments(
        {"razorpay": prov},
        default_provider="razorpay",
        invoicer=Invoicer(seller, store),
        free_features={"seats": 1},
    )


def signed(event: dict, event_id: str, secret: str = WH) -> tuple[bytes, dict]:
    body = json.dumps(event).encode()
    return body, {
        "X-Razorpay-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(),
        "X-Razorpay-Event-Id": event_id,
    }


def captured(payment_id, order_id, amount=11800, org="org1", plan="credits_100", **extra):
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": amount,
                    "currency": "INR",
                    "method": "upi",
                    "email": "buyer@example.com",
                    "notes": {"org_id": org, "plan": plan},
                    **extra,
                }
            }
        },
    }
