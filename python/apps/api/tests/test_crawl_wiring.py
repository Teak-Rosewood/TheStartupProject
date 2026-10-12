"""Website crawling as the sample API wires it: routes, fetching through the job worker, the kill
switch, deletion of a source with its documents, and the internal render API."""

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
    "tsp.crawl",
]
ANN = "aaaaaaaa-0000-0000-0000-00000000000a"
PAGE = (
    "<html><head><title>Cats</title></head><body><main><article><h1>Cats</h1>"
    + "<p>The cat sat on the mat and purred all afternoon in the warm sun by the window.</p>" * 6
    + '<p><a href="/more">more cats</a></p></article></main></body></html>'
)


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


def wait_for(fn, timeout=20):
    end, got = time.time() + timeout, None
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.2)
    raise AssertionError(f"condition not reached; last value: {got!r}")


def fake_site(c):
    """Point the app's crawler at an in-memory site: no network in tests."""
    import httpx

    def handler(request):
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if path in ("/", "/more"):  # different text, or the vector store would dedupe them
            body = PAGE if path == "/" else PAGE.replace("cat", "dog").replace("Cats", "Dogs")
            return httpx.Response(200, headers={"content-type": "text/html"}, text=body)
        return httpx.Response(404)

    async def resolve(host):
        return ["93.184.216.34"]

    http = c.app.state.crawl.http
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http._resolver = resolve


def test_crawl_indexes_a_site_and_deleting_the_source_removes_it(client):
    fake_site(client)
    org = client.post("/api/orgs", json={"name": "Acme"}).json()["id"]
    vbase, cbase = f"/api/orgs/{org}/vector", f"/api/orgs/{org}/crawl"
    assert client.post(f"{vbase}/collections", json={"name": "kb"}).status_code == 201
    r = client.post(
        f"{cbase}/sources",
        json={"collection": "kb", "seed_url": "https://help.test/", "max_depth": 1},
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    pages = wait_for(
        lambda: (
            (p := client.get(f"{cbase}/sources/{sid}/pages").json())
            and len(p) == 2
            and all(x["status"] == "ok" for x in p)
            and p
        )
    )
    assert sorted(x["url"] for x in pages) == ["https://help.test/", "https://help.test/more"]
    docs = wait_for(
        lambda: (
            (d := client.get(f"{vbase}/documents").json())
            and len(d) == 2
            and all(x["status"] == "ready" for x in d)
            and d
        )
    )
    hits = client.post(
        f"{vbase}/search", json={"collection": "kb", "query": "cat on the mat"}
    ).json()
    assert hits["results"] and "cat" in hits["results"][0]["content"]
    # another organization sees neither the source nor its pages
    other = client.post("/api/orgs", json={"name": "Other"}).json()["id"]
    assert client.get(f"/api/orgs/{other}/crawl/sources").json() == []
    assert client.get(f"/api/orgs/{other}/crawl/sources/{sid}/pages").status_code == 404
    assert client.delete(f"{cbase}/sources/{sid}").status_code == 202
    wait_for(lambda: client.get(f"{vbase}/documents").json() == [])
    assert client.get(f"{cbase}/sources").json() == []
    assert len(docs) == 2


def test_kill_switch_stops_new_sources(client):
    org = client.post("/api/orgs", json={"name": "Acme"}).json()["id"]
    cbase = f"/api/orgs/{org}/crawl"
    client.post(f"/api/orgs/{org}/vector/collections", json={"name": "kb"})

    async def kill():
        d = Database(DB)
        async with d.session() as s:
            await s.execute(text("insert into flags (key, enabled) values ('kill:crawl', true)"))
        await d.dispose()

    asyncio.run(kill())
    client.app.state.flags.invalidate()
    r = client.post(f"{cbase}/sources", json={"collection": "kb", "seed_url": "https://help.test/"})
    assert r.status_code == 503 and r.json()["error"]["type"] == "feature_disabled"


def test_render_api_is_mounted_only_when_configured(client, monkeypatch):
    assert client.post("/api/internal/crawl/render/claim").status_code == 404
    import tsp_api.main as main

    monkeypatch.setenv("CRAWL_RENDER", "fargate")
    monkeypatch.setenv("CRAWL_RENDER_TOKEN", "r" * 32)
    monkeypatch.setattr(main, "build_llm", lambda: FakeLLM())
    app = main.build_app()
    with TestClient(app) as c:
        assert c.post("/api/internal/crawl/render/claim").status_code == 401
        ok = c.post(
            "/api/internal/crawl/render/claim", headers={"Authorization": "Bearer " + "r" * 32}
        )
        assert ok.status_code == 204
