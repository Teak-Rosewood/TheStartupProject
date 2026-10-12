from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.jobs import enqueue

from .models import Run, Source


def build_router(
    account: Callable[..., Any],
    session: Callable[..., Any],
    admin: Callable[..., Any],
) -> APIRouter:
    """Platform-admin endpoints. ``admin`` is a dependency that raises 403 for non-admins; every route
    needs it because sources, runs and costs are operator data."""
    r = APIRouter(prefix="/api/sources", dependencies=[Depends(admin)])

    @r.get("")
    async def list_sources(s: AsyncSession = Depends(session)) -> list[dict[str, Any]]:
        rows = (await s.execute(select(Source).order_by(Source.key))).scalars().all()
        return [
            {
                "key": x.key,
                "name": x.name,
                "url": x.url,
                "tier": x.tier,
                "enabled": x.enabled,
                "strategies": x.strategies,
                "schedule": x.schedule,
                "robots_ok": x.robots_ok,
                "terms_checked_at": x.terms_checked_at,
            }
            for x in rows
        ]

    @r.get("/{key}/runs")
    async def runs(
        key: str, limit: int = 50, s: AsyncSession = Depends(session)
    ) -> list[dict[str, Any]]:
        src = (await s.execute(select(Source).where(Source.key == key))).scalar_one_or_none()
        if src is None:
            raise HTTPException(404, "unknown source")
        rows = (
            await s.execute(
                select(Run)
                .where(Run.source_id == src.id)
                .order_by(Run.started_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars()
        return [
            {
                "id": x.id,
                "stream": x.stream,
                "strategy_used": x.strategy_used,
                "status": x.status,
                "started_at": x.started_at,
                "finished_at": x.finished_at,
                "items_new": x.items_new,
                "items_updated": x.items_updated,
                "errors": x.errors,
                "cost": str(x.cost),
                "error_summary": x.error_summary,
            }
            for x in rows
        ]

    @r.post("/{key}/sync", status_code=202)
    async def sync(
        key: str, stream: str | None = None, s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        src = (await s.execute(select(Source).where(Source.key == key))).scalar_one_or_none()
        if src is None:
            raise HTTPException(404, "unknown source")
        if not src.enabled:
            raise HTTPException(409, "source is disabled")
        job_id = await enqueue(s, "sources.sync", {"source_key": key, "stream": stream})
        return {"queued": True, "job_id": job_id}

    return r
