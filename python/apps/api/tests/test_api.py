import asyncio
import os
import uuid
from pathlib import Path

import aioboto3
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from tsp.auth import Claims
from tsp.db import Database, upgrade
from tsp.server import Settings, create_app, current_user
from tsp_api.routes import router

DB = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
CACHE = os.environ.get("TSP_TEST_CACHE_URL", "redis://127.0.0.1:56379/0")
DDB = os.environ.get("TSP_TEST_DYNAMO_URL", "http://127.0.0.1:58000")
MIGRATIONS = Path(__file__).parents[1] / "migrations"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    table = f"api{uuid.uuid4().hex[:8]}"

    async def setup():
        db = Database(DB)
        try:
            await db.ping()
        except Exception:
            pytest.skip("local services not running (docker compose up -d)")
        async with db.session() as s:
            await s.execute(text("DROP TABLE IF EXISTS notes, alembic_version"))
        await db.dispose()
        await asyncio.to_thread(upgrade, DB, MIGRATIONS)
        async with aioboto3.Session().client(
            "dynamodb", region_name="us-east-1", endpoint_url=DDB
        ) as c:
            await c.create_table(
                TableName=table,
                BillingMode="PAY_PER_REQUEST",
                AttributeDefinitions=[
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
            )

    asyncio.run(setup())
    app = create_app(
        Settings(
            database_url=DB,
            cache_url=CACHE,
            dynamodb_table=table,
            dynamodb_endpoint=DDB,
            aws_region="us-east-1",
        )
    )
    app.include_router(router, prefix="/api")
    user = {"sub": "u1"}
    app.dependency_overrides[current_user] = lambda: Claims(
        sub=user["sub"], token_use="access", email="a@b.c"
    )
    with TestClient(app) as c:
        c.user = user
        yield c


def test_me(client):
    assert client.get("/api/me").json()["sub"] == "u1"


def test_notes_are_per_user_and_persisted(client):
    assert client.post("/api/notes", json={"text": "hello"}).status_code == 201
    assert [n["text"] for n in client.get("/api/notes").json()] == ["hello"]
    client.user["sub"] = "u2"
    assert client.get("/api/notes").json() == []


def test_notes_validation(client):
    assert client.post("/api/notes", json={"text": ""}).status_code == 422


def test_kv_roundtrip_in_dynamodb(client):
    assert client.get("/api/kv/theme").status_code == 404
    client.put("/api/kv/theme", json={"value": {"dark": True, "n": 2}})
    assert client.get("/api/kv/theme").json()["value"] == {"dark": True, "n": 2}


def test_hits_counter_uses_cache(client):
    a = client.get("/api/hits").json()["hits"]
    assert client.get("/api/hits").json()["hits"] == a + 1
