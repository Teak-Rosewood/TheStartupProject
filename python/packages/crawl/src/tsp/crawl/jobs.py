from __future__ import annotations

from typing import Any

from .service import FETCH_JOB, PLAN_JOB, PURGE_JOB, REFRESH_JOB, CrawlService


def register_jobs(registry: Any, service: CrawlService) -> None:
    """Add the crawl handlers to a ``tsp.jobs`` registry; ``crawl.refresh`` runs hourly."""

    @registry.job(PLAN_JOB)
    async def plan(ctx: Any, payload: dict[str, Any]) -> None:
        await service.plan(payload["source_id"])

    @registry.job(FETCH_JOB)
    async def fetch(ctx: Any, payload: dict[str, Any]) -> None:
        await service.fetch_page(payload["page_id"])

    @registry.job(REFRESH_JOB)
    async def refresh(ctx: Any, payload: dict[str, Any]) -> None:
        await service.refresh_due()

    @registry.job(PURGE_JOB)
    async def purge(ctx: Any, payload: dict[str, Any]) -> None:
        await service.purge(payload["source_id"])

    registry.schedule("crawl-refresh", "every 1h", REFRESH_JOB)
