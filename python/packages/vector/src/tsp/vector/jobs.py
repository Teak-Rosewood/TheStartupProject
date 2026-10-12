from __future__ import annotations

from typing import Any

from .service import INGEST_JOB, REEMBED_JOB, VectorService


def register_jobs(registry: Any, service: VectorService) -> None:
    """Add the indexing and re-embedding handlers to a ``tsp.jobs`` registry."""

    @registry.job(INGEST_JOB)
    async def ingest(ctx: Any, payload: dict[str, Any]) -> None:
        await service.ingest(payload["document_id"])

    @registry.job(REEMBED_JOB)
    async def reembed(ctx: Any, payload: dict[str, Any]) -> None:
        await service.reembed(payload["collection_id"], payload["model"])
