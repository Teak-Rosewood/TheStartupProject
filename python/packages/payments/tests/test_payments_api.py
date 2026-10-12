import hashlib
import hmac
from types import SimpleNamespace

import pytest
from fastapi import Depends, Header, HTTPException
from fastapi.testclient import TestClient
from payments_support import PLANS, URL, captured, signed
from tsp.payments.api import build_router, require_feature
from tsp.server import Settings, create_app, db_session

ORG = "org1"


@pytest.fixture
async def client(db, pay):
    """A real server app (error envelope, sessions) with stub identity: role comes from a header."""
    await _seed(db, pay)

    async def account():
        return SimpleNamespace(sub="user1", email="buyer@example.com")

    def org_member(minimum):
        order = ["member", "admin", "owner"]

        async def real(org_id: str, x_role: str = Header(default="none")):
            if x_role not in order or order.index(x_role) < order.index(minimum) or org_id != ORG:
                raise HTTPException(404 if org_id != ORG else 403, "no")
            return SimpleNamespace(org_id=org_id, role=x_role)

        return real

    app = create_app(Settings(database_url=URL))
    app.include_router(build_router(pay, account, org_member, db_session), prefix="/api")

    @app.get(
        "/api/orgs/{org_id}/export",
        dependencies=[Depends(require_feature(pay, "exports", db_session))],
    )
    async def export(org_id: str):
        return {"ok": True}

    with TestClient(app) as c:
        c.pay = pay
        yield c


async def _seed(db, pay):
    async with db.session() as s:
        await pay.sync_plans(s, PLANS)


def test_plans_are_public(client):
    codes = {p["code"] for p in client.get("/api/billing/plans").json()}
    assert codes == {"pro_monthly", "credits_100"}


def test_checkout_needs_an_admin_and_returns_sdk_options(client):
    body = {"plan_code": "credits_100"}
    assert (
        client.post(
            f"/api/orgs/{ORG}/billing/checkout", json=body, headers={"x-role": "member"}
        ).status_code
        == 403
    )
    r = client.post(f"/api/orgs/{ORG}/billing/checkout", json=body, headers={"x-role": "admin"})
    assert r.status_code == 201
    out = r.json()
    assert (
        out["kind"] == "order"
        and out["checkout"]["key"] == "rzp_test"
        and out["checkout"]["amount"] == 11800
    )
    assert (
        client.post(
            f"/api/orgs/{ORG}/billing/checkout",
            json={"plan_code": "nope"},
            headers={"x-role": "admin"},
        ).status_code
        == 404
    )


def test_confirm_checks_the_client_signature(client):
    sig = hmac.new(b"secret", b"order_1|pay_1", hashlib.sha256).hexdigest()
    ok = client.post(
        f"/api/orgs/{ORG}/billing/confirm",
        json={"payment_id": "pay_1", "order_id": "order_1", "signature": sig},
        headers={"x-role": "admin"},
    )
    bad = client.post(
        f"/api/orgs/{ORG}/billing/confirm",
        json={"payment_id": "pay_1", "order_id": "order_1", "signature": "0" * 64},
        headers={"x-role": "admin"},
    )
    assert ok.json() == {"verified": True} and bad.json() == {"verified": False}


def test_webhook_endpoint_end_to_end(client):
    co = client.post(
        f"/api/orgs/{ORG}/billing/checkout",
        json={"plan_code": "credits_100"},
        headers={"x-role": "admin"},
    ).json()
    body, headers = signed(captured("pay_w1", co["checkout"]["order_id"]), "evt_w1")
    r = client.post("/api/webhooks/razorpay", content=body, headers=headers)
    assert r.status_code == 200 and r.json() == {"processed": 1, "duplicates": 0}
    again = client.post("/api/webhooks/razorpay", content=body, headers=headers)
    assert again.json() == {"processed": 0, "duplicates": 1}
    overview = client.get(f"/api/orgs/{ORG}/billing", headers={"x-role": "member"}).json()
    assert overview["credits"] == 100 and overview["plan"] == "free"
    invoices = client.get(f"/api/orgs/{ORG}/invoices", headers={"x-role": "admin"}).json()
    assert len(invoices) == 1 and invoices[0]["total"] == 11800
    url = client.get(
        f"/api/orgs/{ORG}/invoices/{invoices[0]['id']}/pdf", headers={"x-role": "admin"}
    ).json()["url"]
    assert url.startswith("local://invoices/")
    assert client.get(f"/api/orgs/{ORG}/invoices", headers={"x-role": "member"}).status_code == 403


def test_webhook_rejects_bad_signatures_and_unknown_providers(client):
    body, headers = signed(captured("p", "o"), "e", secret="attacker")
    r = client.post("/api/webhooks/razorpay", content=body, headers=headers)
    assert r.status_code == 400 and r.json()["error"]["type"] == "invalid_signature"
    assert client.post("/api/webhooks/nobody", content=body, headers=headers).status_code == 404


def test_feature_gate_returns_payment_required(client):
    r = client.get(f"/api/orgs/{ORG}/export")
    assert (
        r.status_code == 402
        and r.json()["error"]["type"] == "upgrade_required"
        and r.json()["error"]["feature"] == "exports"
    )


def test_billing_profile_validates_gstin(client):
    h = {"x-role": "admin"}
    good = {"legal_name": "Buyer Pvt Ltd", "email": "a@b.co", "address": "12 Street, Mumbai"}
    assert (
        client.put(
            f"/api/orgs/{ORG}/billing/profile", json={**good, "gstin": "27AAAPL1234C1ZZ"}, headers=h
        ).status_code
        == 400
    )
    assert client.put(f"/api/orgs/{ORG}/billing/profile", json=good, headers=h).status_code == 200
    assert (
        client.get(f"/api/orgs/{ORG}/billing/profile", headers=h).json()["legal_name"]
        == "Buyer Pvt Ltd"
    )


def test_plans_file_loader(tmp_path):
    from tsp.payments import load_plans

    f = tmp_path / "plans.toml"
    f.write_text(
        '[[plan]]\ncode="a"\nname="A"\nkind="subscription"\namount=100\ninterval="month"\nprovider_plan_id="plan_a"\nfeatures={seats=3}\n'
    )
    (plan,) = load_plans(f)
    assert (plan.code, plan.amount, plan.features) == ("a", 100, {"seats": 3})
    f.write_text('[[plan]]\ncode="a"\nname="A"\nkind="gift"\namount=1\n')
    with pytest.raises(ValueError, match="kind"):
        load_plans(f)
    f.write_text('[[plan]]\ncode="a"\nname="A"\nkind="one_time"\namount=1.5\n')
    with pytest.raises(ValueError, match="minor units"):
        load_plans(f)
