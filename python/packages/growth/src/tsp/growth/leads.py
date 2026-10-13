"""Waitlist with double opt-in: we only keep mailing people who confirmed from their own inbox."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Lead

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


@dataclass
class Captcha:
    """Cloudflare Turnstile or hCaptcha. Unconfigured means off (development)."""

    provider: str = "none"  # none | turnstile | hcaptcha
    secret: str = ""
    http: httpx.AsyncClient | None = None

    _URLS = {
        "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        "hcaptcha": "https://api.hcaptcha.com/siteverify",
    }

    @property
    def enabled(self) -> bool:
        return self.provider in self._URLS and bool(self.secret)

    async def verify(self, token: str | None, ip: str | None = None) -> bool:
        if not self.enabled:
            return True
        if not token:
            return False
        client = self.http or httpx.AsyncClient(timeout=8)
        try:
            data = {"secret": self.secret, "response": token}
            if ip:
                data["remoteip"] = ip
            resp = await client.post(self._URLS[self.provider], data=data)
            return resp.status_code == 200 and bool(resp.json().get("success"))
        except (httpx.HTTPError, ValueError):
            return False  # fail closed: if the check cannot run, nobody signs up through it
        finally:
            if self.http is None:
                await client.aclose()


def ip_hash(ip: str | None, secret: str) -> str | None:
    return hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()[:32] if ip else None


class Leads:
    def __init__(
        self,
        secret: str,
        *,
        consent_text: str,
        send_confirmation: Callable[[AsyncSession, Lead, str], Awaitable[None]] | None = None,
        on_confirmed: Callable[[AsyncSession, Lead], Awaitable[None]] | None = None,
    ):
        self._secret = secret.encode()
        self.consent_text = consent_text
        self.send_confirmation, self.on_confirmed = send_confirmation, on_confirmed

    def token(self, lead_id: int, email: str) -> str:
        body = base64.urlsafe_b64encode(f"{lead_id}:{email}".encode()).rstrip(b"=").decode()
        return f"{body}.{hmac.new(self._secret, b'lead:' + body.encode(), hashlib.sha256).hexdigest()[:32]}"

    def _parse(self, token: str) -> tuple[int, str] | None:
        body, _, sig = token.partition(".")
        if not body or not hmac.compare_digest(
            hmac.new(self._secret, b"lead:" + body.encode(), hashlib.sha256).hexdigest()[:32], sig
        ):
            return None
        try:
            lid, email = (
                base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode().split(":", 1)
            )
            return int(lid), email
        except Exception:
            return None

    async def join(
        self,
        session: AsyncSession,
        email: str,
        *,
        source: str | None,
        attribution: dict[str, Any],
        ip: str | None,
    ) -> bool:
        """Record a signup and send the confirmation. Returns False for an invalid address.

        The caller always shows the same "check your inbox" message, so the response never reveals
        whether an address was already on the list.
        """
        email = email.strip().lower()
        if not _EMAIL.match(email) or len(email) > 254:
            return False
        row = await session.execute(
            insert(Lead)
            .values(
                email=email,
                source=(source or "")[:60] or None,
                attribution=_clean_attribution(attribution),
                consent_text=self.consent_text,
                consent_ip_hash=ip_hash(ip, self._secret.decode()),
            )
            .on_conflict_do_nothing(index_elements=["email"])
            .returning(Lead.id)
        )
        found = row.first()
        lead = (
            await session.get(Lead, found[0])
            if found
            else (await session.execute(select(Lead).where(Lead.email == email))).scalar_one()
        )
        assert lead is not None
        if lead.confirmed_at is None and self.send_confirmation:
            await self.send_confirmation(session, lead, self.token(lead.id, lead.email))
        return True

    async def confirm(self, session: AsyncSession, token: str) -> Lead | None:
        parsed = self._parse(token)
        if parsed is None:
            return None
        lead = await session.get(Lead, parsed[0])
        if lead is None or lead.email != parsed[1]:
            return None
        if lead.confirmed_at is None:
            lead.confirmed_at, lead.unsubscribed_at = datetime.now(UTC), None
            if self.on_confirmed:
                await self.on_confirmed(session, lead)
        return lead


_ATTR_KEYS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "referrer",
    "landing_path",
    "ref",
)


def _clean_attribution(raw: dict[str, Any]) -> dict[str, Any]:
    return {k: str(raw[k])[:200] for k in _ATTR_KEYS if raw.get(k)}
