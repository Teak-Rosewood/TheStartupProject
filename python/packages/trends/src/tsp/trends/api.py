from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CandidateRow
from .service import TrendsError, TrendsService


class Decision(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


def _candidate(c: CandidateRow) -> dict[str, Any]:
    return {
        "id": c.id, "day": c.day, "label": c.label, "topic_id": c.topic_id, "score": c.score,
        "components": c.components, "status": c.status, "picked_by": c.picked_by, "note": c.note,
    }  # fmt: skip


def build_router(
    svc: TrendsService,
    account: Callable[..., Any],
    org_member: Callable[[str], Callable[..., Any]],
    session: Callable[..., Any],
) -> APIRouter:
    """Routes under ``/orgs/{org_id}/trends``. Members read the list; editors (role ``member`` here,
    pass a stricter ``org_member`` if you need one) pick and dismiss."""
    r = APIRouter(prefix="/orgs/{org_id}/trends")
    member = org_member("member")

    @r.get("/candidates")
    async def candidates(
        org_id: str,
        day: date | None = None,
        _: Any = Depends(member),
        s: AsyncSession = Depends(session),
    ) -> list[dict[str, Any]]:
        d = day or (datetime.now(timezone.utc) - timedelta(days=1)).date()
        return [_candidate(c) for c in await svc.candidates(s, org_id, d)]

    async def decide(
        org_id: str, cid: str, body: Decision, u: Any, s: AsyncSession, pick: bool
    ) -> dict[str, Any]:
        try:
            fn = svc.pick if pick else svc.dismiss
            return _candidate(await fn(s, org_id, cid, u.sub, body.note))
        except TrendsError as e:
            raise HTTPException(e.status, detail={"type": e.type, "message": str(e)}) from e

    @r.post("/candidates/{cid}/pick")
    async def pick(
        org_id: str, cid: str, body: Decision | None = None, u: Any = Depends(account),
        _: Any = Depends(member), s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:  # fmt: skip
        return await decide(org_id, cid, body or Decision(), u, s, True)

    @r.post("/candidates/{cid}/dismiss")
    async def dismiss(
        org_id: str, cid: str, body: Decision | None = None, u: Any = Depends(account),
        _: Any = Depends(member), s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:  # fmt: skip
        return await decide(org_id, cid, body or Decision(), u, s, False)

    return r
