"""Public growth endpoints. Everything that accepts anonymous input is rate limited and validated."""

from __future__ import annotations

import hashlib
import html
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import client_ip
from tsp.cache import Cache

from . import events as ev
from . import referrals
from .leads import Captcha, Leads


class JoinIn(BaseModel):
    email: str = Field(max_length=254)
    source: str | None = Field(default=None, max_length=60)
    attribution: dict[str, Any] = {}
    consent: bool = False
    captcha_token: str | None = None
    website: str = ""  # honeypot: real people never see or fill this field


class EventsIn(BaseModel):
    events: list[dict[str, Any]] = Field(max_length=ev.MAX_BATCH)


class IdentifyIn(BaseModel):
    anonymous_id: str


class AttributionIn(BaseModel):
    attribution: dict[str, Any] = {}
    referral_code: str | None = Field(default=None, max_length=16)


def _opted_out(request: Request) -> bool:
    """Honour browser privacy signals (Global Privacy Control, Do Not Track)."""
    return request.headers.get("sec-gpc") == "1" or request.headers.get("dnt") == "1"


def build_router(
    leads: Leads,
    captcha: Captcha,
    cache: Callable[..., Cache],  # dependency returning the Cache (tsp.server.get_cache)
    account: Callable[..., Any],  # dependency: signed-in local user (.sub)
    optional_sub: Callable[
        ..., Any
    ],  # dependency: user sub or None (public routes that can use a login)
    session: Callable[..., Any],
    *,
    set_attribution: Callable[[AsyncSession, str, dict[str, Any]], Any] | None = None,
    on_referral: Callable[[AsyncSession, str, str], Any] | None = None,
) -> APIRouter:
    r = APIRouter()

    async def limit(c: Cache, key: str, n: int, window: int) -> None:
        if not await c.allow(key, n, window):
            raise HTTPException(
                429,
                detail={"type": "rate_limited", "message": "too many requests, try again later"},
            )

    @r.post("/leads", status_code=202)
    async def join(
        body: JoinIn,
        request: Request,
        c: Cache = Depends(cache),
        s: AsyncSession = Depends(session),
    ) -> dict[str, str]:
        ip = client_ip(request)
        await limit(c, f"lead-ip:{ip}", 10, 3600)
        if body.website:  # a bot filled the honeypot: pretend it worked
            return {"status": "check_your_inbox"}
        if not body.consent:
            raise HTTPException(
                400,
                detail={
                    "type": "consent_required",
                    "message": "please tick the box to join the list",
                },
            )
        await limit(
            c,
            "lead-email:" + hashlib.sha256(body.email.strip().lower().encode()).hexdigest()[:24],
            3,
            86400,
        )
        if not await captcha.verify(body.captcha_token, ip):
            raise HTTPException(
                400,
                detail={
                    "type": "captcha_failed",
                    "message": "could not verify you are human; try again",
                },
            )
        if not await leads.join(
            s, body.email, source=body.source, attribution=body.attribution, ip=ip
        ):
            raise HTTPException(
                422,
                detail={
                    "type": "invalid_email",
                    "message": "that does not look like an email address",
                },
            )
        return {
            "status": "check_your_inbox"
        }  # the same answer whether or not the address was already listed

    @r.get("/leads/confirm", response_class=HTMLResponse)
    async def confirm_page(token: str = Query(...)) -> str:
        """Asks first: mail scanners open links, and a scanner must not confirm someone's signup."""
        if leads._parse(token) is None:
            raise HTTPException(
                400,
                detail={"type": "invalid_token", "message": "this confirmation link is not valid"},
            )
        return (
            "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><h1>Confirm your email</h1>"
            f"<form method=post action='/api/leads/confirm?token={html.escape(token)}'><button>Yes, add me to the list</button></form>"
        )

    @r.post("/leads/confirm")
    async def confirm(
        token: str = Query(...), s: AsyncSession = Depends(session)
    ) -> dict[str, str]:
        lead = await leads.confirm(s, token)
        if lead is None:
            raise HTTPException(
                400,
                detail={"type": "invalid_token", "message": "this confirmation link is not valid"},
            )
        return {"status": "confirmed"}

    @r.post("/events", status_code=202)
    async def collect(
        body: EventsIn,
        request: Request,
        sub: Any = Depends(optional_sub),
        c: Cache = Depends(cache),
        s: AsyncSession = Depends(session),
    ) -> dict[str, int]:
        if _opted_out(request):
            return {"stored": 0}
        await limit(c, f"events-ip:{client_ip(request)}", 120, 60)
        try:
            return {"stored": await ev.store(s, body.events, user_sub=sub)}
        except ev.InvalidEvent as e:
            raise HTTPException(422, detail={"type": "invalid_event", "message": str(e)}) from e

    @r.post("/events/identify")
    async def identify(
        body: IdentifyIn, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, int]:
        try:
            return {"linked": await ev.identify(s, body.anonymous_id, u.sub)}
        except ev.InvalidEvent as e:
            raise HTTPException(422, detail={"type": "invalid_event", "message": str(e)}) from e

    @r.post("/me/attribution")
    async def attribute(
        body: AttributionIn, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        """First-touch attribution and referral credit, sent once after sign-up."""
        from .leads import _clean_attribution

        clean = _clean_attribution(body.attribution)
        if set_attribution and clean:
            await set_attribution(s, u.sub, clean)
        credited = bool(
            body.referral_code and await referrals.record(s, body.referral_code, u.sub, on_referral)
        )
        return {"attribution_saved": bool(clean), "referral_credited": credited}

    @r.get("/me/referral")
    async def my_referral(
        u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        return {
            "code": await referrals.code_for(s, u.sub),
            "referred": await referrals.count(s, u.sub),
        }

    return r
