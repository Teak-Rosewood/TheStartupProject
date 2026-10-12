import asyncio
import os

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tsp.crawl import HttpFetcher
from tsp.db import Database, normalize_url, upgrade

ADMIN_URL = os.environ.get(
    "TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test"
)
DB_NAME = "tsp_trends_test"  # private database: other suites drop shared tables
URL = ADMIN_URL.rsplit("/", 1)[0] + "/" + DB_NAME


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
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.audit", "tsp.sources", "tsp.trends"])
    yield d
    await d.dispose()


@pytest.fixture
def web():
    """A fake web: ``web.pages[url] = (status, body)``; returns an HttpFetcher over it."""

    class Web:
        def __init__(self):
            self.pages: dict[str, tuple[int, str]] = {}

        def handler(self, request):
            # fetch_raw connects to the pinned IP and keeps the real name in the Host header
            q = f"?{request.url.query.decode()}" if request.url.query else ""
            url = f"{request.url.scheme}://{request.headers['host']}{request.url.path}{q}"
            status, body = self.pages.get(url, (404, ""))
            return httpx.Response(status, content=body.encode())

        async def resolve(self, host):
            return ["93.184.216.34"]

        def fetcher(self):
            return HttpFetcher(
                httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
                resolver=self.resolve,
            )

    return Web()
