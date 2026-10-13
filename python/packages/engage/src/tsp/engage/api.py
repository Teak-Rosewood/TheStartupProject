from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from .config import EngageConfig
from .polls import PollError
from .receipts import TooManyPings
from .service import Engage


class PingBody(BaseModel):
    org_id: str
    dwell: float = Field(ge=0, le=86400)
    scroll: float = Field(ge=0, le=1)
    reached_end: bool = False


class AnswerBody(BaseModel):
    phase: str
    x: float
    y: float | None = None


def build_router(
    account: Callable[..., Any],
    session: Callable[..., Any],
    cfg: EngageConfig | None = None,
    *,
    allow_ping: Callable[[str], Any] | None = None,
) -> APIRouter:
    """Signed-in readers only. ``org_id`` is the publishing organization (a namespace, not a
    permission). Serving the full item calls ``Receipts.start`` on the server; the page sends pings."""
    cfg = cfg or EngageConfig()
    r = APIRouter(prefix="/engage")

    def engage(s: AsyncSession) -> Engage:
        return Engage(s, cfg, allow_ping=allow_ping)

    @r.get("/me")
    async def me(
        org_id: str, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        return await engage(s).me(u.sub, org_id)

    @r.post("/receipts/{item_id}/ping")
    async def ping(
        item_id: str, body: PingBody, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        e = engage(s)
        try:
            await e.receipts.ping(u.sub, item_id, body.dwell, body.scroll, body.reached_end)
        except TooManyPings as ex:
            raise HTTPException(429, "too many pings") from ex
        except LookupError as ex:
            raise HTTPException(404, str(ex)) from ex
        out = await e.record_read(u.sub, body.org_id, item_id)
        return {
            "read": out.newly_read,
            "points": out.points,
            "badges": out.badges,
            "streak_week_met": out.streak_week_met,
        }

    @r.get("/polls")
    async def polls_for_item(
        org_id: str, item_id: str, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> list[dict[str, Any]]:
        e = engage(s)
        out = []
        for p in await e.polls.for_item(org_id, item_id):
            out.append(
                {
                    "id": str(p.id),
                    "kind": p.kind,
                    "axes": p.axes,
                    "opens_at": p.opens_at,
                    "closes_at": p.closes_at,
                    "shift": await e.polls.shift(u.sub, p.id),
                }
            )
        return out

    @r.post("/polls/{poll_id}/answers", status_code=201)
    async def answer(
        poll_id: uuid.UUID,
        body: AnswerBody,
        u: Any = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        e = engage(s)
        try:
            paid = await e.respond_to_poll(u.sub, poll_id, body.phase, body.x, body.y)
        except LookupError as ex:
            raise HTTPException(404, str(ex)) from ex
        except PollError as ex:
            raise HTTPException(422, detail={"type": "poll_rejected", "message": str(ex)}) from ex
        return {"points": paid, "shift": await e.polls.shift(u.sub, poll_id)}

    @r.get("/polls/{poll_id}/aggregate")
    async def aggregate(
        poll_id: uuid.UUID, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        e = engage(s)
        try:
            return await e.polls.aggregate(poll_id)
        except LookupError as ex:
            raise HTTPException(404, str(ex)) from ex

    @r.post("/onboarding/{step}")
    async def onboarding(
        step: str, org_id: str, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        e = engage(s)
        try:
            paid = await e.complete_onboarding(u.sub, org_id, step)
        except ValueError as ex:
            raise HTTPException(404, str(ex)) from ex
        return {"points": paid, "remaining": await e.onboarding.remaining(u.sub, org_id)}

    return r
