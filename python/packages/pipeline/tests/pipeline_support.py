import os
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tsp.ai.models import ChatCompletionResponse, Choice, ChoiceMessage, Usage
from tsp.db import normalize_url
from tsp.jobs import JobRegistry, Worker
from tsp.pipeline import Price, Runner

BASE_URL = os.environ.get(
    "TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test"
)
# A database of its own: other suites drop shared tables while they run.
URL = BASE_URL.rsplit("/", 1)[0] + "/tsp_pipeline_test"
TABLES = (
    "pl_eval_runs, pl_events, pl_steps, pl_prompt_active, pl_prompts, pl_runs, jobs, cron_jobs, flags, "
    "audit_log, alembic_version"
)
PRICES = {"m-cheap": Price(10.0, 10.0), "m-big": Price(1000.0, 1000.0)}  # $ per million tokens


class FakeLLM:
    """Replies from a queue (then ``"ok"``); every call is recorded."""

    def __init__(self, replies=(), tin=1000, tout=500):
        self.replies, self.tin, self.tout, self.calls = list(replies), tin, tout, []
        self.chat = SimpleNamespace(create=self.create)

    async def create(self, model, messages, **params):
        self.calls.append((model, messages, params))
        reply = self.replies.pop(0) if self.replies else "ok"
        return ChatCompletionResponse(
            model=model,
            routed_via=f"fake/{model}",
            usage=Usage(
                prompt_tokens=self.tin,
                completion_tokens=self.tout,
                total_tokens=self.tin + self.tout,
            ),
            choices=[Choice(message=ChoiceMessage(content=reply))],
        )


async def _ensure_database() -> None:
    engine = create_async_engine(normalize_url(BASE_URL), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as c:
            if not (
                await c.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = 'tsp_pipeline_test'")
                )
            ).first():
                await c.execute(text("CREATE DATABASE tsp_pipeline_test TEMPLATE template0"))
    finally:
        await engine.dispose()


async def _drop(db) -> None:
    async with db.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(f"DROP TABLE IF EXISTS {TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))


class Harness:
    """A runner plus a worker over one job registry, like one app process."""

    def __init__(self, db, workflows, llm=None, flags=None, lease=timedelta(minutes=10)):
        self.db, self.llm = db, llm or FakeLLM()
        self.runner = Runner(
            db,
            workflows,
            llm=self.llm,
            prices=PRICES,
            flags=flags,
            retry_delay=lambda attempt: timedelta(0),
        )
        reg = JobRegistry()
        self.runner.register(reg)
        self.worker = Worker(reg, db, poll_interval=0.01, lease=lease)

    async def drain(self, limit=50):
        """Run jobs until none are due."""
        for _ in range(limit):
            if not await self.worker.run_once():
                return
        raise AssertionError("jobs never settled")
