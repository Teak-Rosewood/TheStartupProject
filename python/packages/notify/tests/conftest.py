import asyncio
import os

import pytest
from sqlalchemy import text
from tsp.db import Database, upgrade
from tsp.notify import Notifier, Outgoing, Templates, TransportError

URL = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
TABLES = "emails, email_suppressions, email_preferences, device_tokens, notifications, jobs, cron_jobs, alembic_version"


async def _drop(d):
    async with d.session() as s:
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))


@pytest.fixture
async def db(tmp_path):
    d = Database(URL, pool_size=10)
    try:
        await d.ping()
    except Exception:
        pytest.skip("no Postgres at TSP_TEST_DATABASE_URL")
    await _drop(d)
    app = tmp_path / "migrations"
    (app / "versions").mkdir(parents=True)
    (app / "env.py").write_text("from tsp.db import run_alembic_env\nrun_alembic_env(None)\n")
    (app / "script.py.mako").write_text("")
    await asyncio.to_thread(upgrade, URL, app, modules=["tsp.jobs", "tsp.notify"])
    yield d
    await _drop(d)
    await d.dispose()


class FakeTransport:
    def __init__(self):
        self.sent: list[Outgoing] = []
        self.errors: list[Exception] = []

    async def send(self, mail: Outgoing) -> str:
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(mail)
        return f"mid-{len(self.sent)}"


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def notifier(transport):
    return Notifier(
        transport,
        Templates(),
        sender="Acme <no-reply@example.com>",
        brand="Acme",
        base_url="https://app.example.com",
        secret="s" * 32,
        footer="Acme Technologies Pvt Ltd, Bengaluru",
    )


__all__ = ["TransportError"]
