from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .series import SeriesService
from .service import GraphService


def build_router(org_of: Callable[..., Any], session: Callable[..., Any]) -> APIRouter:
    """Read-only graph API for the signed-in organization.

    ``org_of`` is a dependency returning the caller's organization id (it should refuse callers who
    belong to none); ``session`` yields an ``AsyncSession``.
    """
    r = APIRouter(prefix="/graph")

    @r.get("/entities")
    async def entities(
        q: Annotated[str, Query(min_length=1, max_length=200)],
        kind: str | None = None,
        org: str = Depends(org_of),
        s: AsyncSession = Depends(session),
    ) -> list[dict[str, Any]]:
        found = await GraphService(s).search_entities(org, q, kind)
        return [{"id": str(e.id), "kind": e.kind, "name": e.name, "attrs": e.attrs} for e in found]

    @r.get("/entities/{entity_id}/neighbours")
    async def neighbours(
        entity_id: uuid.UUID,
        depth: Annotated[int, Query(ge=1, le=3)] = 1,
        at: date | None = None,
        rel: Annotated[list[str] | None, Query()] = None,
        org: str = Depends(org_of),
        s: AsyncSession = Depends(session),
    ) -> list[dict[str, Any]]:
        edges = await GraphService(s).neighbours(org, ("entity", entity_id), rel, depth, at)
        return [
            {
                "id": str(e.id),
                "src": {"kind": e.src[0], "id": str(e.src[1])},
                "rel": e.rel,
                "dst": {"kind": e.dst[0], "id": str(e.dst[1])},
                "valid_from": e.valid_from,
                "valid_to": e.valid_to,
                "depth": e.depth,
            }
            for e in edges
        ]

    @r.get("/series/{key}")
    async def series(
        key: str,
        start: date | None = None,
        end: date | None = None,
        estimate_type: str = "latest",
        org: str = Depends(org_of),
        s: AsyncSession = Depends(session),
    ) -> list[dict[str, Any]]:
        rows = await SeriesService(s).get(org, key, start, end, estimate_type)
        if not rows:
            raise HTTPException(404, "no observations")
        return [
            {
                "period_start": o.period_start,
                "period_end": o.period_end,
                "value": float(o.value),
                "estimate_type": o.estimate_type,
                "vintage": o.vintage,
            }
            for o in rows
        ]

    return r
