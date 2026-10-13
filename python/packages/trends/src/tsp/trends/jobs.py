from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .service import TrendsService


def register_jobs(registry: Any, service: TrendsService) -> None:
    """``trends.collect`` hourly (all signals, all organizations), ``trends.scan`` daily at 05:00 UTC
    for the day just ended, ``trends.prune`` daily."""

    @registry.job("trends.collect")
    async def collect(ctx: Any, payload: dict[str, Any]) -> None:
        await service.collect_all()

    @registry.job("trends.scan")
    async def scan(ctx: Any, payload: dict[str, Any]) -> None:
        day = (datetime.now(UTC) - timedelta(days=1)).date()
        await service.scan_all(day)

    @registry.job("trends.prune")
    async def prune(ctx: Any, payload: dict[str, Any]) -> None:
        await service.prune()

    registry.schedule("trends-collect", "every 1h", "trends.collect")
    registry.schedule("trends-scan", "daily 05:00", "trends.scan")
    registry.schedule("trends-prune", "daily 06:00", "trends.prune")
