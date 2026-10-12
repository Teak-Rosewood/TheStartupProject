import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from api_support import ALL_TABLES
from sqlalchemy import text
from tsp.ai import BatchRequest, BatchResult, BatchStatus, ChatCompletionResponse, ProviderHttpError
from tsp.ai.models import Choice, ChoiceMessage
from tsp.db import Database, upgrade
from tsp_api.ai_batches import batch_results, collect_batches, register_batch_jobs, submit_batch

DB = os.environ.get("TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test")
MIGRATIONS = Path(__file__).parents[1] / "migrations"


class FakeBatches:
    def __init__(self):
        self.ended, self.unknown = set(), set()
        self.counts = {"processing": 1}

    async def submit(self, requests):
        return f"batch_{len(requests)}_{requests[0].custom_id}"

    async def poll(self, batch_id):
        if batch_id in self.unknown:
            raise ProviderHttpError("not found", status=404)
        done = batch_id in self.ended
        return BatchStatus(
            batch_id, "ended" if done else "in_progress", {"succeeded": 1} if done else self.counts
        )

    async def results(self, batch_id):
        resp = ChatCompletionResponse(
            model="m", choices=[Choice(message=ChoiceMessage(content="done"))]
        )
        return [BatchResult("a", "succeeded", resp), BatchResult("b", "errored", None, "too long")]


async def _drop(d):
    async with d.session() as s:
        await s.execute(text("ALTER TABLE IF EXISTS audit_log DISABLE TRIGGER USER"))
        await s.execute(text(f"DROP TABLE IF EXISTS {ALL_TABLES} CASCADE"))
        await s.execute(text("DROP FUNCTION IF EXISTS tsp_audit_log_immutable() CASCADE"))


@pytest.fixture
async def db():
    d = Database(DB)
    try:
        await d.ping()
    except Exception:
        pytest.skip("local services not running (docker compose up -d)")
    await _drop(d)
    await asyncio.to_thread(upgrade, DB, MIGRATIONS, modules=["tsp.jobs"])
    yield d
    await _drop(d)
    await d.dispose()


@pytest.fixture
def llm():
    return SimpleNamespace(batches=FakeBatches())


async def test_submitted_batches_are_collected_once_they_end(db, llm):
    reqs = [BatchRequest("a", "m", []), BatchRequest("b", "m", [])]
    async with db.session() as s:
        bid = await submit_batch(s, llm, "org-1", reqs, note="summaries")
    async with db.session() as s:
        assert await collect_batches(s, llm) == 0  # still running: only the counts are refreshed
        row = await batch_results(s, "org-1", bid)
    assert (
        row["status"] == "submitted"
        and row["counts"] == {"processing": 1}
        and row["results"] is None
    )

    llm.batches.ended.add(bid)
    async with db.session() as s:
        assert await collect_batches(s, llm) == 1
        assert await collect_batches(s, llm) == 0  # collected batches are not polled again
        row = await batch_results(s, "org-1", bid)
        assert await batch_results(s, "org-2", bid) is None  # another organization cannot read it
    assert row["status"] == "collected" and row["note"] == "summaries"
    by_id = {r["custom_id"]: r for r in row["results"]}
    assert by_id["a"]["response"]["choices"][0]["message"]["content"] == "done"
    assert (by_id["b"]["status"], by_id["b"]["error"]) == ("errored", "too long")


async def test_a_batch_the_provider_forgot_is_marked_failed_not_polled_forever(db, llm):
    async with db.session() as s:
        bid = await submit_batch(s, llm, "org-1", [BatchRequest("z", "m", [])])
    llm.batches.unknown.add(bid)
    async with db.session() as s:
        assert await collect_batches(s, llm) == 0
        row = await batch_results(s, "org-1", bid)
    assert row["status"] == "failed" and "not found" in row["results"]["error"]


def test_the_collect_job_is_scheduled_every_five_minutes(llm):
    from tsp.jobs import JobRegistry

    reg = JobRegistry()
    register_batch_jobs(reg, llm)
    assert "ai.batch.collect" in reg.handlers and reg.schedules["ai-batch-collect"][0] == "every 5m"
