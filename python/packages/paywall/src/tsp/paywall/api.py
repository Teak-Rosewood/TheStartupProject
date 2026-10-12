from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import client_ip

from .meter import COOKIE, MAX_AGE_S
from .service import Decision, InsufficientCredits, Item, Paywall, Visitor


class ItemIn(BaseModel):
    org_id: str
    item_id: str = Field(min_length=1, max_length=200)
    type: str = Field(default="article", max_length=40)


class GiftIn(ItemIn):
    max_uses: int = Field(default=3, ge=1, le=20)
    days: int = Field(default=7, ge=1, le=30)


def visitor_from_request(request: Request, user_id: str | None = None) -> Visitor:
    return Visitor(
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
        accept_language=request.headers.get("accept-language", ""),
        meter_cookie=request.cookies.get(COOKIE),
        gift=request.query_params.get("g"),
        user_id=user_id,
    )


def _out(d: Decision) -> dict[str, Any]:
    return {
        "allowed": d.allowed,
        "reason": d.reason,
        "remaining": d.remaining,
        "wall": d.wall,
        "require_account": d.require_account,
    }


def build_router(
    pw: Paywall,
    account: Callable[..., Any],
    optional_user: Callable[..., Any],
    session: Callable[..., Any],
    *,
    secure_cookie: bool = True,
) -> APIRouter:
    """``GET /paywall/status`` and ``POST /paywall/read`` work for anyone (``optional_user`` returns a
    user id or None); gifts and unlocks need a signed-in account. Call ``read`` when the page is shown."""
    r = APIRouter(prefix="/paywall")

    def stamp(resp: Response, d: Decision) -> None:
        if d.set_cookie:
            resp.set_cookie(
                COOKIE,
                d.set_cookie,
                max_age=MAX_AGE_S,
                httponly=True,
                samesite="lax",
                secure=secure_cookie,
            )
        resp.headers["Cache-Control"] = "private, no-store"

    @r.get("/status")
    async def status(
        request: Request,
        response: Response,
        org_id: str,
        item: str,
        type: str = "article",
        uid: str | None = Depends(optional_user),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        d = await pw.check(s, visitor_from_request(request, uid), Item(item, org_id, type))
        stamp(response, d)
        return _out(d)

    @r.post("/read")
    async def read(
        body: ItemIn,
        request: Request,
        response: Response,
        uid: str | None = Depends(optional_user),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        v, it = visitor_from_request(request, uid), Item(body.item_id, body.org_id, body.type)
        d = await pw.check(s, v, it)
        await pw.record_read(s, v, it, d)
        stamp(response, d)
        return _out(d)

    @r.post("/gifts", status_code=201)
    async def gift(
        body: GiftIn, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, str]:
        try:
            token = await pw.create_gift(
                s,
                u.sub,
                Item(body.item_id, body.org_id, body.type),
                max_uses=body.max_uses,
                days=body.days,
            )
        except ValueError as e:
            raise HTTPException(429, detail={"type": "rate_limited", "message": str(e)}) from e
        return {"token": token}

    @r.post("/unlock")
    async def unlock(
        body: ItemIn, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        try:
            new = await pw.unlock(s, u.sub, Item(body.item_id, body.org_id, body.type))
        except InsufficientCredits as e:
            raise HTTPException(
                402, detail={"type": "insufficient_credits", "message": str(e)}
            ) from e
        return {
            "unlocked": True,
            "newly_unlocked": new,
            "credits": await pw.balance(s, u.sub, body.org_id),
        }

    return r
