import asyncio
import os
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text
from tsp.accounts import Accounts
from tsp.admin import AdminSettings, mount_admin
from tsp.compliance import Compliance
from tsp.db import Database, upgrade
from tsp.flags import Flags
from tsp.payments import Invoicer, LocalInvoiceStore, Payments, RazorpayProvider, Seller
from tsp.server import Settings, create_app

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
ISS = "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_STAFF"
CLIENT = "staff-client"
DOMAIN = "https://tsp-staff.auth.ap-south-1.amazoncognito.com"
TABLES = (
    "credit_ledger, credit_accounts, invoices, invoice_sequences, webhook_events, transfers, refunds, payments, subscriptions, "
    "payment_customers, plans, billing_profiles, leads, events, referral_codes, referrals, flags, usage_records, api_keys, invites, memberships, orgs, users, consents, data_requests, audit_log, alembic_version"
)
MODULES = [
    "tsp.audit",
    "tsp.accounts",
    "tsp.compliance",
    "tsp.payments",
    "tsp.growth",
    "tsp.flags",
    "tsp.metering",
]


async def _drop(d):
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))


class FakeIdentity:
    def __init__(self):
        self.calls = []

    async def sign_out(self, sub):
        self.calls.append(("sign_out", sub))

    async def disable(self, sub):
        self.calls.append(("disable", sub))

    async def delete(self, sub):
        self.calls.append(("delete", sub))


class Staff:
    """Test harness: a fake Cognito (JWKS + token endpoint) and helpers to sign in as a role."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        self.jwks = {"keys": [{**jwk, "kid": "k1", "alg": "RS256"}]}
        self.groups, self.sub, self.email = (
            ["support"],
            "aaaaaaaa-0000-0000-0000-000000000001",
            "staff@example.com",
        )
        self.nonce = None
        self.token_requests = []
        self.token_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("jwks.json"):
            return httpx.Response(200, json=self.jwks)
        if request.url.path == "/oauth2/token":
            self.token_requests.append(request)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            now = int(time.time())
            tok = jwt.encode(
                {
                    "sub": self.sub,
                    "iss": ISS,
                    "aud": CLIENT,
                    "exp": now + 300,
                    "iat": now,
                    "token_use": "id",
                    "email": self.email,
                    "cognito:groups": self.groups,
                    "nonce": self.nonce,
                },
                self.key,
                algorithm="RS256",
                headers={"kid": "k1"},
            )
            return httpx.Response(
                200, json={"id_token": tok, "access_token": "x", "token_type": "Bearer"}
            )
        return httpx.Response(404)


@pytest.fixture
def staff():
    return Staff()


@pytest.fixture
def env(tmp_path, staff):
    async def setup():
        d = Database(URL)
        try:
            await d.ping()
        except Exception:
            pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
        await _drop(d)
        app_dir = tmp_path / "migrations"
        (app_dir / "versions").mkdir(parents=True)
        (app_dir / "env.py").write_text(
            "from tsp.db import run_alembic_env\nrun_alembic_env(None)\n"
        )
        (app_dir / "script.py.mako").write_text("")
        await asyncio.to_thread(upgrade, URL, app_dir, modules=MODULES)
        await d.dispose()

    asyncio.run(setup())
    identity = FakeIdentity()
    accounts = Accounts()
    compliance = Compliance(None, identity=identity)
    accounts.register_with_compliance(compliance)
    gateway_calls = []

    def gateway(req: httpx.Request) -> httpx.Response:
        gateway_calls.append((req.url.path, req.content))
        return httpx.Response(200, json={"id": "rfnd_1", "amount": 500, "status": "processed"})

    from tsp.payments.gst import valid_gstin  # noqa: F401

    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    body = "29AAAPL1234C1Z"
    total = sum(
        (lambda v: v // 36 + v % 36)(chars.index(c) * (1 if i % 2 == 0 else 2))
        for i, c in enumerate(body)
    )
    gstin = body + chars[(36 - total % 36) % 36]
    payments = Payments(
        {
            "razorpay": RazorpayProvider(
                "k", "s", "w", http=httpx.AsyncClient(transport=httpx.MockTransport(gateway))
            )
        },
        default_provider="razorpay",
        invoicer=Invoicer(
            Seller("Acme", gstin, "Bengaluru", "29", "998314"), LocalInvoiceStore(tmp_path / "inv")
        ),
    )
    app = create_app(Settings(database_url=URL))
    settings = AdminSettings(
        issuer=ISS,
        client_id=CLIENT,
        client_secret="s" * 40,
        domain=DOMAIN,
        external_url="https://app.example.com",
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(staff.handler))
    mount_admin(
        app,
        settings=settings,
        database_url=URL,
        accounts=accounts,
        compliance=compliance,
        payments=payments,
        flags=Flags(ttl=0),
        http=http,
    )
    with TestClient(app, base_url="https://app.example.com", follow_redirects=False) as client:
        client.identity, client.accounts, client.compliance, client.settings = (
            identity,
            accounts,
            compliance,
            settings,
        )
        client.payments, client.gateway_calls = payments, gateway_calls
        yield client

    async def cleanup():
        d = Database(URL)
        await _drop(d)
        await d.dispose()

    asyncio.run(cleanup())


def sign_in(client, staff, groups=None):
    """Run the full redirect dance as the fake staff user."""
    if groups is not None:
        staff.groups = groups
    r = client.get("/api/admin/oidc/start")
    assert r.status_code == 303 and r.headers["location"].startswith(DOMAIN + "/oauth2/authorize?")
    q = parse_qs(urlparse(r.headers["location"]).query)
    staff.nonce = q["nonce"][0]
    return client.get("/api/admin/oidc/callback", params={"code": "abc", "state": q["state"][0]}), q
