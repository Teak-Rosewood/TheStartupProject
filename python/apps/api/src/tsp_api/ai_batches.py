"""Message Batches in the app: remember each submitted batch, collect its results when it ends."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.ai import BatchRequest, LLMClient

log = logging.getLogger("tsp_api.ai_batches")
COLLECT = "ai.batch.collect"


async def submit_batch(
    session: AsyncSession,
    client: LLMClient,
    org_id: str,
    requests: list[BatchRequest],
    note: str = "",
) -> str:
    """Submit to the provider and record the batch in the caller's transaction. Poll with
    :func:`batch_results`; the ``ai.batch.collect`` job fills the results in once the batch ends."""
    batch_id = await client.batches.submit(requests)
    await session.execute(
        text("INSERT INTO ai_batches (id, org_id, note) VALUES (:i, :o, :n)"),
        {"i": batch_id, "o": org_id, "n": note},
    )
    return batch_id


async def batch_results(session: AsyncSession, org_id: str, batch_id: str) -> dict[str, Any] | None:
    """The stored row for one of the organization's batches (status, counts, results), or None."""
    row = (
        await session.execute(
            text(
                "SELECT id, status, counts, results, note FROM ai_batches WHERE id = :i AND org_id = :o"
            ),
            {"i": batch_id, "o": org_id},
        )
    ).first()
    return dict(row._mapping) if row else None


async def collect_batches(session: AsyncSession, client: LLMClient, limit: int = 20) -> int:
    """Poll batches that are still open; store the results of the ones that ended. Returns how many ended.

    A batch the provider no longer knows is marked ``failed`` rather than polled forever.
    """
    rows = (
        await session.execute(
            text(
                "SELECT id FROM ai_batches WHERE status = 'submitted' ORDER BY submitted_at LIMIT :n FOR UPDATE SKIP LOCKED"
            ),
            {"n": limit},
        )
    ).all()
    ended = 0
    for (batch_id,) in rows:
        try:
            status = await client.batches.poll(batch_id)
            if not status.done:
                await session.execute(
                    text("UPDATE ai_batches SET counts = CAST(:c AS jsonb) WHERE id = :i"),
                    {"c": json.dumps(status.counts), "i": batch_id},
                )
                continue
            results = [
                {
                    "custom_id": r.custom_id,
                    "status": r.status,
                    "error": r.error,
                    "response": r.response.model_dump(exclude_none=True) if r.response else None,
                }
                for r in await client.batches.results(batch_id)
            ]
        except Exception as e:  # unknown batch, expired key...: stop polling it, keep the reason
            log.warning("batch %s could not be collected: %s", batch_id, e)
            await session.execute(
                text(
                    "UPDATE ai_batches SET status = 'failed', results = CAST(:r AS jsonb) WHERE id = :i"
                ),
                {"r": json.dumps({"error": str(e)[:500]}), "i": batch_id},
            )
            continue
        await session.execute(
            text(
                "UPDATE ai_batches SET status = 'collected', counts = CAST(:c AS jsonb), "
                "results = CAST(:r AS jsonb), collected_at = now() WHERE id = :i"
            ),
            {"c": json.dumps(status.counts), "r": json.dumps(results), "i": batch_id},
        )
        ended += 1
    return ended


def register_batch_jobs(registry: Any, client: LLMClient) -> None:
    """``ai.batch.collect`` every 5 minutes."""

    @registry.job(COLLECT)
    async def collect(ctx: Any, payload: dict[str, Any]) -> None:
        n = await collect_batches(ctx.session, client)
        if n:
            log.info("collected %s finished batches", n)

    registry.schedule("ai-batch-collect", "every 5m", COLLECT)
