"""Routes: unsubscribe (one click), email preferences, device tokens, in-app notifications."""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import inapp
from .mailer import Notifier
from .models import EmailPreference, Notification


class PrefsIn(BaseModel):
    marketing: bool


class DeviceIn(BaseModel):
    token: str = Field(min_length=10, max_length=4096)
    platform: str = Field(pattern="^(android|ios|web)$")


class ReadIn(BaseModel):
    ids: list[int] | None = None


def build_router(
    notifier: Notifier, account: Callable[..., Any], session: Callable[..., Any]
) -> APIRouter:
    """``account`` is a dependency returning an object with ``.sub`` and ``.email``."""
    r = APIRouter()

    def _email(token: str) -> str:
        email = notifier.email_from_token(token)
        if email is None:
            raise HTTPException(
                400,
                detail={"type": "invalid_token", "message": "this unsubscribe link is not valid"},
            )
        return email

    @r.get("/email/unsubscribe", response_class=HTMLResponse)
    async def unsubscribe_page(token: str = Query(...)) -> str:
        """A confirmation page, not an action: mail scanners follow links and must not unsubscribe people."""
        email = _email(token)
        return (
            "<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<h1>Unsubscribe</h1><p>Stop marketing emails to <b>{html.escape(email)}</b>?</p>"
            f"<form method=post action='/api/email/unsubscribe?token={html.escape(token)}'><button>Unsubscribe</button></form>"
            "<p>You will still get essential messages about your account.</p>"
        )

    @r.post("/email/unsubscribe", response_class=PlainTextResponse)
    async def unsubscribe(token: str = Query(...), s: AsyncSession = Depends(session)) -> str:
        """Also the RFC 8058 one-click target mail clients POST to."""
        await notifier.set_marketing(s, _email(token), False)
        return "You have been unsubscribed from marketing email."

    @r.get("/me/email-preferences")
    async def get_prefs(
        u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        pref = await s.get(EmailPreference, (u.email or "").lower()) if u.email else None
        return {"email": u.email, "marketing": bool(pref and pref.marketing)}

    @r.put("/me/email-preferences")
    async def put_prefs(
        body: PrefsIn, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, Any]:
        if not u.email:
            raise HTTPException(
                400,
                detail={"type": "email_unknown", "message": "no verified email on this account"},
            )
        await notifier.set_marketing(s, u.email, body.marketing)
        return {"email": u.email, "marketing": body.marketing}

    @r.put("/me/devices", status_code=204)
    async def add_device(
        body: DeviceIn, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> None:
        await inapp.register_device(s, u.sub, body.token, body.platform)

    @r.delete("/me/devices/{token}", status_code=204)
    async def remove_device(
        token: str, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> None:
        from .models import DeviceToken

        row = await s.get(DeviceToken, token)
        if row is not None and row.user_sub == u.sub:
            await s.delete(row)

    @r.get("/me/notifications")
    async def notifications(
        unread: bool = False,
        limit: int = Query(30, le=100),
        u: Any = Depends(account),
        s: AsyncSession = Depends(session),
    ) -> dict[str, Any]:
        q = (
            select(Notification)
            .where(Notification.user_sub == u.sub)
            .order_by(Notification.id.desc())
            .limit(limit)
        )
        if unread:
            q = q.where(Notification.read_at.is_(None))
        rows = (await s.execute(q)).scalars().all()
        return {
            "unread": await inapp.unread_count(s, u.sub),
            "items": [
                {
                    "id": n.id,
                    "kind": n.kind,
                    "title": n.title,
                    "body": n.body,
                    "url": n.url,
                    "created_at": n.created_at,
                    "read": n.read_at is not None,
                }
                for n in rows
            ],
        }

    @r.post("/me/notifications/read")
    async def read(
        body: ReadIn, u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, int]:
        return {"marked": await inapp.mark_read(s, u.sub, body.ids)}

    return r
