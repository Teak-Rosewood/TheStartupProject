"""The vector module as the sample API wires it: routes, indexing through the job worker, plan
limits, the kill switch, and removal with the account."""

import asyncio
import hashlib
import math
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

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
    "tsp.vector",
]
ANN = "aaaaaaaa-0000-0000-0000-00000000000a"
DOC = "The cat sat on the mat and purred.\n\nTomatoes need sun and water every day."


def vec(t):
    v = [0.0] * 1024
    for w in re.findall(r"[a-z]+", t.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 1024] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class FakeEmbeddings:
    async def create(self, model, input, *, dims=None, input_type="document", batch_size=96):
        data = [SimpleNamespace(index=i, embedding=vec(t)) for i, t in enumerate(input)]
        return SimpleNamespace(
            data=data, usage=SimpleNamespace(total_tokens=sum(len(t) // 4 for t in input))
        )


class FakeLLM:
    embeddings = FakeEmbeddings()

    async def close(self):
        pass


async def _drop():
    d = Database(DB)
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
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

    @asynccontextmanager
    async def nothing(app):
        yield

    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setattr(main, "build_llm", lambda: FakeLLM())
    monkeypatch.setattr(main, "register_catalog_jobs", lambda *a, **k: None)
    monkeypatch.setattr(main, "catalog_lifespan", lambda llm: lambda app: nothing(app))
    monkeypatch.setenv("TSP_JOBS_INLINE", "1")
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


def wait_ready(c, base, n=1, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        docs = c.get(f"{base}/documents").json()
        if len(docs) >= n and all(d["status"] in ("ready", "failed") for d in docs):
            return docs
        time.sleep(0.2)
    raise AssertionError(f"documents did not finish: {docs}")


def test_index_through_the_worker_search_and_delete(client):
    org = client.post("/api/orgs", json={"name": "Acme"}).json()["id"]
    base = f"/api/orgs/{org}/vector"
    assert client.post(f"{base}/collections", json={"name": "kb"}).status_code == 201
    r = client.post(f"{base}/documents", json={"collection": "kb", "text": DOC, "title": "Pets"})
    assert r.status_code == 202
    (doc,) = wait_ready(client, base)
    assert doc["status"] == "ready", doc
    hits = client.post(
        f"{base}/search", json={"collection": "kb", "query": "where did the cat sit"}
    ).json()["results"]
    assert hits and "cat sat" in hits[0]["content"] and hits[0]["title"] == "Pets"
    # a second organization cannot reach it
    other = client.post("/api/orgs", json={"name": "Other"}).json()["id"]
    assert (
        client.post(
            f"/api/orgs/{other}/vector/search", json={"collection": "kb", "query": "cat"}
        ).status_code
        == 404
    )
    assert client.delete(f"{base}/documents/{doc['id']}").status_code == 204
    assert (
        client.post(f"{base}/search", json={"collection": "kb", "query": "cat"}).json()["results"]
        == []
    )


def test_kill_switch_and_usage_are_wired(client):
    org = client.post("/api/orgs", json={"name": "Acme"}).json()["id"]
    base = f"/api/orgs/{org}/vector"
    client.post(f"{base}/collections", json={"name": "kb"})
    client.post(f"{base}/documents", json={"collection": "kb", "text": DOC})
    wait_ready(client, base)
    client.post(f"{base}/search", json={"collection": "kb", "query": "cat"})

    async def read():
        d = Database(DB)
        async with d.session() as s:
            kinds = (
                (await s.execute(text("select kind from usage_records order by id")))
                .scalars()
                .all()
            )
            await s.execute(text("insert into flags (key, enabled) values ('kill:vector', true)"))
        await d.dispose()
        return kinds

    assert sorted(set(asyncio.run(read()))) == ["ai.embed", "vector.search"]
    client.app.state.flags.invalidate()
    r = client.post(f"{base}/search", json={"collection": "kb", "query": "cat"})
    assert r.status_code == 503 and r.json()["error"]["type"] == "feature_disabled"
