from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from .service import summary


def build_router(
    org_member: Callable[[str], Callable[..., Any]], session: Callable[..., Any]
) -> APIRouter:
    r = APIRouter()
    admin = org_member("admin")

    @r.get("/orgs/{org_id}/usage")
    async def usage(
        org_id: str,
        days: int = Query(30, ge=1, le=366),
        _m: Any = Depends(admin),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        return await summary(s, org_id, days)

    return r
