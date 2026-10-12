import asyncio
import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tsp.cache import NullCache
from tsp.crawl import HttpFetcher
from tsp.db import Database, normalize_url, upgrade
from tsp.sources import LocalRawStore

ADMIN_URL = os.environ.get(
    "TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test"
)
# Own database: other suites drop shared tables and alembic_version in the shared one.
DB_NAME = "tsp_sources_test"
URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + DB_NAME
FIXTURES = Path(__file__).parent / "fixtures"


async def _ensure_database() -> bool:
    engine = create_async_engine(normalize_url(ADMIN_URL), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as c:
            exists = (
                await c.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": DB_NAME}
                )
            ).first()
            if not exists:
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
        upgrade, URL, app, modules=["tsp.audit", "tsp.jobs", "tsp.metering", "tsp.sources"]
    )
    yield d
    await d.dispose()


@pytest.fixture
def store(tmp_path):
    return LocalRawStore(tmp_path / "raw")


class Site:
    """A fake website: route table of path -> (status, content-type, body), plus a request log."""

    def __init__(self):
        self.routes: dict[str, tuple[int, str, bytes]] = {}
        self.log: list[str] = []
        self.auth: list[str | None] = []
        self.fail_after: int | None = None  # raise on the Nth request, to simulate a crash

    def html(self, path, fixture, status=200):
        self.routes[path] = (status, "text/html", (FIXTURES / fixture).read_bytes())

    def json(self, path, doc, status=200):
        import json

        self.routes[path] = (status, "application/json", json.dumps(doc).encode())

    def handler(self, request: httpx.Request) -> httpx.Response:
        key = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        self.log.append(key)
        self.auth.append(request.headers.get("authorization"))
        if self.fail_after is not None and len(self.log) > self.fail_after:
            raise RuntimeError("simulated crash")
        status, ctype, body = self.routes.get(key) or self.routes.get(
            request.url.path, (404, "text/plain", b"")
        )
        return httpx.Response(status, headers={"content-type": ctype}, content=body)


@pytest.fixture
def site():
    s = Site()
    s.routes["/robots.txt"] = (200, "text/plain", b"User-agent: *\nAllow: /\n")
    return s


@pytest.fixture
def http(site):
    async def resolve(host):
        return ["93.184.216.34"]

    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
    return HttpFetcher(client, resolver=resolve)


@pytest.fixture
def cache():
    return NullCache()
