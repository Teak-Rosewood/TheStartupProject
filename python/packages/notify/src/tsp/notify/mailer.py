from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import aioboto3
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.jobs import enqueue

from .models import Email, EmailPreference, Suppression
from .templating import Rendered, Templates

log = logging.getLogger("tsp.notify")
SEND_JOB = "notify.send_email"
TRANSACTIONAL, MARKETING = "transactional", "marketing"


class TransportError(Exception):
    """``permanent`` errors (bad address, rejected content) are not retried."""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


@dataclass
class Outgoing:
    to: str
    sender: str
    message: Rendered
    category: str
    headers: dict[str, str] = field(default_factory=dict)


class EmailTransport(Protocol):
    async def send(self, mail: Outgoing) -> str: ...


class SesTransport:
    """Amazon SES v2. Marketing and transactional mail use separate configuration sets so a
    complaint on a newsletter cannot damage the reputation of your receipts."""

    def __init__(
        self,
        region: str | None = None,
        transactional_set: str | None = None,
        marketing_set: str | None = None,
    ):
        self._region, self._sets = (
            region,
            {TRANSACTIONAL: transactional_set, MARKETING: marketing_set},
        )
        self._session = aioboto3.Session()

    async def send(self, mail: Outgoing) -> str:
        params: dict[str, Any] = {
            "FromEmailAddress": mail.sender,
            "Destination": {"ToAddresses": [mail.to]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": mail.message.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": mail.message.html, "Charset": "UTF-8"},
                        "Text": {"Data": mail.message.text, "Charset": "UTF-8"},
                    },
                    "Headers": [{"Name": k, "Value": v} for k, v in mail.headers.items()],
                }
            },
        }
        if self._sets.get(mail.category):
            params["ConfigurationSetName"] = self._sets[mail.category]
        async with self._session.client("sesv2", region_name=self._region) as ses:
            try:
                resp = await ses.send_email(**params)
            except Exception as e:  # botocore ClientError: classify by the SES error code
                code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
                permanent = code in {
                    "MessageRejected",
                    "BadRequestException",
                    "NotFoundException",
                    "AccountSuspendedException",
                    "MailFromDomainNotVerifiedException",
                }
                raise TransportError(f"{code or type(e).__name__}: {e}", permanent=permanent) from e
        return str(resp["MessageId"])


def _now() -> datetime:
    return datetime.now(UTC)


class Notifier:
    """Queue, render and send email with suppression and marketing consent enforced at send time."""

    def __init__(
        self,
        transport: EmailTransport,
        templates: Templates,
        *,
        sender: str,
        brand: str,
        base_url: str,
        secret: str,
        footer: str = "",
    ):
        self.transport, self.templates = transport, templates
        self.sender, self.brand, self.base_url, self.footer = (
            sender,
            brand,
            base_url.rstrip("/"),
            footer,
        )
        self._secret = secret.encode()

    # -- unsubscribe tokens ------------------------------------------------------------------

    def unsubscribe_token(self, email: str) -> str:
        """Self-contained, tamper-proof, no expiry: an unsubscribe link must keep working."""
        body = base64.urlsafe_b64encode(email.lower().encode()).rstrip(b"=")
        sig = hmac.new(self._secret, b"unsub:" + body, hashlib.sha256).hexdigest()[:32]
        return f"{body.decode()}.{sig}"

    def email_from_token(self, token: str) -> str | None:
        body, _, sig = token.partition(".")
        good = hmac.new(self._secret, b"unsub:" + body.encode(), hashlib.sha256).hexdigest()[:32]
        if not body or not hmac.compare_digest(good, sig):
            return None
        try:
            return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        except Exception:
            return None

    # -- state ---------------------------------------------------------------------------------

    async def is_suppressed(
        self, session: AsyncSession, email: str, *, marketing: bool = False
    ) -> bool:
        row = await session.get(Suppression, email.lower())
        if row is None:
            return False
        return marketing or row.reason in (
            "bounce",
            "complaint",
        )  # an unsubscribe only blocks marketing

    async def suppress(self, session: AsyncSession, email: str, reason: str) -> None:
        await session.execute(
            insert(Suppression).values(email=email.lower(), reason=reason).on_conflict_do_nothing()
        )

    async def set_marketing(self, session: AsyncSession, email: str, allowed: bool) -> None:
        email = email.lower()
        stmt = insert(EmailPreference).values(email=email, marketing=allowed)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["email"], set_={"marketing": allowed, "updated_at": _now()}
            )
        )
        if allowed:
            existing = await session.get(Suppression, email)
            if existing and existing.reason == "unsubscribed":
                await session.delete(existing)
        else:
            await self.suppress(session, email, "unsubscribed")

    async def marketing_allowed(self, session: AsyncSession, email: str) -> bool:
        pref = await session.get(EmailPreference, email.lower())
        return bool(pref and pref.marketing) and not await self.is_suppressed(
            session, email, marketing=True
        )

    # -- queue and send -------------------------------------------------------------------------

    async def queue(
        self,
        session: AsyncSession,
        to: str,
        template: str,
        ctx: dict[str, Any],
        *,
        category: str = TRANSACTIONAL,
    ) -> str:
        """Write the message to the outbox and schedule delivery, in the caller's transaction."""
        if category not in (TRANSACTIONAL, MARKETING):
            raise ValueError("category must be transactional or marketing")
        self.templates.render(
            template, self._context(to, category, ctx)
        )  # fail now, not at 3 a.m. in the worker
        mail = Email(to_addr=to.strip().lower(), template=template, context=ctx, category=category)
        session.add(mail)
        await session.flush()
        await enqueue(session, SEND_JOB, {"email_id": mail.id}, unique_key=f"email:{mail.id}")
        return mail.id

    def _context(self, to: str, category: str, ctx: dict[str, Any]) -> dict[str, Any]:
        unsub = (
            f"{self.base_url}/api/email/unsubscribe?token={self.unsubscribe_token(to)}"
            if category == MARKETING
            else None
        )
        return {"brand": self.brand, "footer": self.footer, "unsubscribe_url": unsub, **ctx}

    async def deliver(self, session: AsyncSession, email_id: str) -> str:
        """Send one queued message (the ``notify.send_email`` job). Returns the final status."""
        mail = (
            await session.execute(select(Email).where(Email.id == email_id).with_for_update())
        ).scalar_one_or_none()
        if mail is None or mail.status != "queued":
            return (
                mail.status if mail else "missing"
            )  # already handled: a retry after success must not resend
        marketing = mail.category == MARKETING
        if await self.is_suppressed(session, mail.to_addr, marketing=marketing) or (
            marketing and not await self.marketing_allowed(session, mail.to_addr)
        ):
            mail.status = "suppressed"
            return mail.status
        ctx = self._context(mail.to_addr, mail.category, mail.context)
        headers = {}
        if marketing:
            headers = {
                "List-Unsubscribe": f"<{ctx['unsubscribe_url']}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        try:
            mid = await self.transport.send(
                Outgoing(
                    mail.to_addr,
                    self.sender,
                    self.templates.render(mail.template, ctx),
                    mail.category,
                    headers,
                )
            )
        except TransportError as e:
            if e.permanent:
                mail.status, mail.error = "failed", str(e)[:500]
                return mail.status
            raise
        mail.status, mail.provider_message_id, mail.sent_at = "sent", mid, _now()
        return mail.status

    # -- bounce and complaint events (SES -> SNS -> SQS) -----------------------------------------

    async def handle_ses_event(self, session: AsyncSession, body: str) -> int:
        """Apply one SES event notification (raw SES JSON, or the SNS envelope around it). Returns addresses suppressed."""
        import json

        data = json.loads(body)
        if isinstance(data.get("Message"), str):  # SNS envelope
            data = json.loads(data["Message"])
        kind = (data.get("eventType") or data.get("notificationType") or "").lower()
        addrs: list[str] = []
        if (
            kind == "bounce" and data.get("bounce", {}).get("bounceType") == "Permanent"
        ):  # transient bounces are retried by SES
            addrs = [r["emailAddress"] for r in data["bounce"].get("bouncedRecipients", [])]
        elif kind == "complaint":
            addrs = [r["emailAddress"] for r in data["complaint"].get("complainedRecipients", [])]
        for a in addrs:
            await self.suppress(session, a, "complaint" if kind == "complaint" else "bounce")
        return len(addrs)


def register_jobs(
    registry: Any,
    notifier: Notifier,
    *,
    ses_queue_url: str | None = None,
    region: str | None = None,
) -> None:
    """Add the delivery job (and, with a queue URL, a bounce/complaint poller every minute) to a registry."""

    @registry.job(SEND_JOB)
    async def send_email(ctx: Any, payload: dict[str, Any]) -> None:
        await notifier.deliver(ctx.session, payload["email_id"])

    if ses_queue_url:
        from .events import poll_ses_events

        @registry.job("notify.poll_ses_events")
        async def poll(ctx: Any, payload: dict[str, Any]) -> None:
            await poll_ses_events(notifier, ctx.database, ses_queue_url, region=region)

        registry.schedule("notify-ses-events", "every 1m", "notify.poll_ses_events")
