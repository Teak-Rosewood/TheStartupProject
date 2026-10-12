import asyncio
import hashlib
import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tsp.cache import NullCache
from tsp.crawl import CrawlService, CrawlSettings, HttpFetcher, RobotsCache
from tsp.db import Database, normalize_url, upgrade
from tsp.jobs import JobRegistry
from tsp.vector import Embedded, PgVectorStore, VectorConfig, VectorService
from tsp.vector import register_jobs as register_vector_jobs

ADMIN_URL = os.environ.get(
    "TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test"
)
DB_NAME = "tsp_crawl_test"  # own database: other suites drop shared tables in the shared one
URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + DB_NAME
FIXTURES = Path(__file__).parent / "fixtures"
DIMS = 1024


async def _ensure_database() -> bool:
    engine = create_async_engine(normalize_url(ADMIN_URL), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as c:
            if not (
                await c.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": DB_NAME}
                )
            ).first():
                await c.execute(text(f'CREATE DATABASE "{DB_NAME}" TEMPLATE template0'))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()


@pytest.fixture
async def db(tmp_path):
    if not await _ensure_database():
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    d = Database(URL)
    async with d.session() as s:
        await s.execute(text("DROP SCHEMA public CASCADE"))
        await s.execute(text("CREATE SCHEMA public"))
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(
        upgrade,
        URL,
        app,
        modules=["tsp.audit", "tsp.jobs", "tsp.metering", "tsp.storage", "tsp.vector", "tsp.crawl"],
    )
    yield d
    await d.dispose()


class Site:
    """A fake website: path -> (status, content type, body), plus the request log."""

    def __init__(self):
        self.routes = {"/robots.txt": (200, "text/plain", b"User-agent: *\nAllow: /\n")}
        self.log = []

    def html(self, path, fixture, status=200):
        self.routes[path] = (status, "text/html", (FIXTURES / fixture).read_bytes())

    def text(self, path, body, ctype="text/html", status=200):
        self.routes[path] = (status, ctype, body.encode())

    def handler(self, request):
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        self.log.append(key)
        status, ctype, body = self.routes.get(key) or self.routes.get(
            request.url.path, (404, "text/plain", b"")
        )
        headers = {"content-type": ctype}
        if status == 200 and request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        if status == 200:
            headers["etag"] = '"v1"'
        return httpx.Response(status, headers=headers, content=body)

    def fetched(self):
        return [p for p in self.log if p != "/robots.txt"]


@pytest.fixture
def site():
    return Site()


async def fake_embedder(texts, model, kind):
    def vec(t):
        d = hashlib.sha256(t.encode()).digest()
        v = [float(b) for b in (d * 33)[:DIMS]]
        n = sum(x * x for x in v) ** 0.5
        return [x / n for x in v]

    return Embedded([vec(t) for t in texts], tokens=sum(len(t) // 4 for t in texts))


@pytest.fixture
def make_service(db, site):
    """Build a CrawlService (and a job registry wired for both modules) over the fake site."""

    def build(*, limits=None, resolver=None, settings=None):
        async def resolve(host):
            return ["93.184.216.34"]

        http = HttpFetcher(
            httpx.AsyncClient(transport=httpx.MockTransport(site.handler)),
            resolver=resolver or resolve,
        )
        cache = NullCache()

        async def fetch_text(url):
            raw = await http.fetch_raw(url)
            return raw.status, raw.body.decode()

        vector = VectorService(
            PgVectorStore(db),
            fake_embedder,
            db,
            VectorConfig(dims=DIMS, embed_model="m1"),
            limits=limits,
        )
        svc = CrawlService(
            vector, http, http, RobotsCache(cache, fetch_text), cache, db,
            settings or CrawlSettings(rps=1000), limits,
        )  # fmt: skip
        registry = JobRegistry()
        from tsp.crawl import register_jobs

        register_jobs(registry, svc)
        register_vector_jobs(registry, vector)
        return svc, registry

    return build
