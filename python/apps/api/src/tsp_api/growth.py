"""Wire waitlist, events and attribution into the app."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.accounts import User
from tsp.growth import Captcha, Leads
from tsp.growth.models import Lead
from tsp.notify import Notifier

DEFAULT_CONSENT = (
    "I agree to receive launch updates and product news by email. I can unsubscribe at any time."
)


def build_leads(notifier: Notifier, env: Mapping[str, str] | None = None) -> tuple[Leads, Captcha]:
    e = os.environ if env is None else env
    app_url = notifier.base_url

    async def send_confirmation(session: AsyncSession, lead: Lead, token: str) -> None:
        await notifier.queue(
            session,
            lead.email,
            "lead_confirm",
            {"confirm_url": f"{app_url}/api/leads/confirm?token={token}"},
        )

    async def on_confirmed(session: AsyncSession, lead: Lead) -> None:
        await notifier.set_marketing(
            session, lead.email, True
        )  # a confirmed signup is recorded marketing consent

    leads = Leads(
        e.get("NOTIFY_SECRET") or "dev-only-notify-secret-change-me-0123456789",
        consent_text=e.get("LEAD_CONSENT_TEXT", DEFAULT_CONSENT),
        send_confirmation=send_confirmation,
        on_confirmed=on_confirmed,
    )
    return leads, Captcha(e.get("CAPTCHA_PROVIDER", "none"), e.get("CAPTCHA_SECRET", ""))


async def optional_sub(request: Request) -> str | None:
    """The signed-in user's id if a valid token came with the request, else None (public endpoints)."""
    verifier = getattr(request.app.state, "verifier", None)
    auth = request.headers.get("authorization", "")
    if verifier is None or not auth.lower().startswith("bearer "):
        return None
    try:
        return str((await verifier.verify(auth[7:])).sub)
    except Exception:
        return None


async def set_first_touch(session: AsyncSession, sub: str, attribution: dict[str, Any]) -> None:
    """First-touch attribution: only written if the user has none yet."""
    user = await session.get(User, sub)
    if user is not None and not user.attribution:
        user.attribution = attribution
