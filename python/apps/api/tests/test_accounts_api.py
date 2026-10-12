import asyncio
import os
from pathlib import Path

import pytest
from api_support import ALL_TABLES
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from tsp.accounts import Accounts, ApiKeyInfo
from tsp.accounts import api as accounts_api
from tsp.auth import Claims
from tsp.db import Database, upgrade
from tsp.server import Settings, create_app, current_user, db_session

DB = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
MIGRATIONS = Path(__file__).parents[1] / "migrations"
TABLES = ALL_TABLES
ANN, BEA = "aaaaaaaa-0000-0000-0000-00000000000a", "bbbbbbbb-0000-0000-0000-00000000000b"


async def _drop():
    d = Database(DB)
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))
    await d.dispose()


@pytest.fixture
def client():
    try:
        asyncio.run(_drop())
    except Exception:
        pytest.skip("local services not running (docker compose up -d)")
    asyncio.run(
        asyncio.to_thread(
            upgrade, DB, MIGRATIONS, modules=["tsp.audit", "tsp.compliance", "tsp.accounts"]
        )
    )
    accounts = Accounts()
    api = accounts_api.build(accounts, current_user, db_session)
    app = create_app(Settings(database_url=DB))
    app.include_router(api.router, prefix="/api")

    @app.get("/api/keyed")
    async def keyed(info: ApiKeyInfo = Depends(api.api_key)):
        return {"org": info.org_id, "scopes": info.scopes}

    who = {"sub": ANN, "email": "ann@example.com"}
    app.dependency_overrides[current_user] = lambda: Claims(
        sub=who["sub"],
        token_use="id",
        email=who["email"],
        raw={"email": who["email"], "email_verified": True, "name": "Someone"},
    )
    with TestClient(app) as c:
        c.who = who
        yield c
    asyncio.run(_drop())


def test_first_request_creates_the_profile(client):
    me = client.get("/api/me").json()
    assert (me["sub"], me["email"], me["organizations"]) == (ANN, "ann@example.com", [])


def test_team_flow_end_to_end(client):
    org = client.post("/api/orgs", json={"name": "Acme Inc"}).json()
    assert org["role"] == "owner"
    inv = client.post(
        f"/api/orgs/{org['id']}/invites", json={"email": "bea@example.com", "role": "member"}
    ).json()
    client.who.update(sub=BEA, email="bea@example.com")
    assert client.get("/api/orgs").json() == []
    assert (
        client.get(f"/api/orgs/{org['id']}/members").status_code == 404
    )  # not a member: org is invisible
    assert (
        client.post("/api/invites/accept", json={"token": inv["token"]}).json()["role"] == "member"
    )
    assert {m["sub"] for m in client.get(f"/api/orgs/{org['id']}/members").json()} == {ANN, BEA}
    # a plain member cannot invite or manage keys
    assert (
        client.post(f"/api/orgs/{org['id']}/invites", json={"email": "x@example.com"}).status_code
        == 403
    )
    assert client.post(f"/api/orgs/{org['id']}/api-keys", json={"name": "k"}).status_code == 403
    client.who.update(sub=ANN, email="ann@example.com")
    assert (
        client.patch(f"/api/orgs/{org['id']}/members/{BEA}", json={"role": "admin"}).status_code
        == 200
    )
    assert client.delete(f"/api/orgs/{org['id']}/members/{ANN}").status_code == 409  # last owner


def test_invite_for_someone_else_is_refused(client):
    org = client.post("/api/orgs", json={"name": "Acme"}).json()
    inv = client.post(f"/api/orgs/{org['id']}/invites", json={"email": "bea@example.com"}).json()
    client.who.update(sub=BEA, email="mallory@example.com")
    r = client.post("/api/invites/accept", json={"token": inv["token"]})
    assert r.status_code == 400 and r.json()["error"]["type"] == "invalid_invite"


def test_customer_api_keys_authenticate_machine_calls(client):
    org = client.post("/api/orgs", json={"name": "Acme"}).json()
    key = client.post(
        f"/api/orgs/{org['id']}/api-keys", json={"name": "ci", "scopes": ["chat"]}
    ).json()
    raw = key["key"]
    listed = client.get(f"/api/orgs/{org['id']}/api-keys").json()
    assert listed[0]["prefix"] and raw not in str(listed)  # the secret is shown once only
    assert client.get("/api/keyed", headers={"Authorization": f"Bearer {raw}"}).json() == {
        "org": org["id"],
        "scopes": ["chat"],
    }
    assert client.get("/api/keyed", headers={"X-API-Key": raw}).status_code == 200
    assert client.get("/api/keyed").status_code == 401
    client.delete(f"/api/orgs/{org['id']}/api-keys/{key['id']}")
    assert client.get("/api/keyed", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_disabled_accounts_are_locked_out(client):
    client.get("/api/me")

    async def disable():
        from tsp.audit import Actor

        d = Database(DB)
        async with d.session() as s:
            await Accounts().set_disabled(
                s, ANN, True, actor=Actor("staff", "support"), reason="test reason"
            )
        await d.dispose()

    asyncio.run(disable())
    r = client.get("/api/me")
    assert r.status_code == 403 and r.json()["error"]["type"] == "account_disabled"
