"""Wire email into the app: the notifier, the hooks that send mail, and the background job registry."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.accounts import Invite, Org, User
from tsp.cms import templates_dir as cms_templates_dir
from tsp.compliance import DataRequest
from tsp.jobs import JobRegistry
from tsp.notify import Notifier, Outgoing, SesTransport, Templates
from tsp.notify import register_jobs as register_notify_jobs
from tsp.payments import BillingProfile, Invoice, Payment

log = logging.getLogger("tsp_api.notify")


class LogTransport:
    """Development stand-in: prints what would be sent so signup and invite flows work without SES."""

    async def send(self, mail: Outgoing) -> str:
        log.info("email to=%s subject=%r category=%s", mail.to, mail.message.subject, mail.category)
        return "dev-" + mail.to


def build_notifier(env: Mapping[str, str] | None = None) -> Notifier:
    e = os.environ if env is None else env
    app_url = e.get("APP_URL", e.get("ADMIN_EXTERNAL_URL", "http://localhost:8000"))
    transport = (
        SesTransport(
            e.get("AWS_REGION"),
            e.get("SES_CONFIG_SET_TRANSACTIONAL"),
            e.get("SES_CONFIG_SET_MARKETING"),
        )
        if e.get("SES_FROM")
        else LogTransport()
    )
    return Notifier(
        transport,
        Templates(cms_templates_dir()),
        sender=e.get("SES_FROM", "dev <no-reply@localhost>"),
        brand=e.get("BRAND_NAME", "Your product"),
        base_url=app_url,
        secret=e.get("NOTIFY_SECRET") or "dev-only-notify-secret-change-me-0123456789",
        footer=e.get("EMAIL_FOOTER", ""),
    )


class Hooks:
    """Email sent when things happen. Each hook runs in a savepoint and can never break the action itself."""

    def __init__(self, notifier: Notifier, app_url: str):
        self.n, self.app_url = notifier, app_url.rstrip("/")

    async def user_created(self, s: AsyncSession, user: User) -> None:
        if user.email:
            await self.n.queue(
                s, user.email, "welcome", {"name": user.name, "cta_url": self.app_url}
            )

    async def invite(self, s: AsyncSession, inv: Invite, token: str, org: Org) -> None:
        inviter = await s.get(User, inv.invited_by)
        await self.n.queue(
            s,
            inv.email,
            "invite",
            {
                "inviter": (inviter.name or inviter.email)
                if inviter and (inviter.name or inviter.email)
                else "A teammate",
                "org": org.name,
                "accept_url": f"{self.app_url}/invite?token={token}",
                "days": 7,
            },
        )

    async def receipt(self, s: AsyncSession, pay: Payment, inv: Invoice) -> None:
        to = (inv.buyer or {}).get("email")
        if to:
            await self.n.queue(
                s,
                to,
                "receipt",
                {
                    "invoice_number": inv.number,
                    "amount": f"{inv.currency} {Decimal(inv.total) / 100:,.2f}",
                    "description": inv.description,
                    "invoice_url": None,
                },
            )

    async def payment_failed(self, s: AsyncSession, pay: Payment) -> None:
        profile = await s.get(BillingProfile, pay.org_id)
        if profile:
            await self.n.queue(
                s,
                profile.email,
                "payment_failed",
                {
                    "description": pay.plan_code or "your plan",
                    "billing_url": f"{self.app_url}/billing",
                },
            )

    async def deletion_requested(self, s: AsyncSession, sub: str, req: DataRequest) -> None:
        user = await s.get(User, sub)
        if user and user.email and req.scheduled_for:
            await self.n.queue(
                s,
                user.email,
                "deletion_scheduled",
                {"date": req.scheduled_for.strftime("%d %B %Y")},
            )


def build_registry(
    notifier: Notifier,
    *,
    compliance: Any,
    payments: Any | None,
    env: Mapping[str, str] | None = None,
) -> JobRegistry:
    e = os.environ if env is None else env
    reg = JobRegistry()
    register_notify_jobs(
        reg,
        notifier,
        ses_queue_url=e.get("SES_EVENTS_QUEUE_URL") or None,
        region=e.get("AWS_REGION"),
    )

    @reg.job("compliance.run_due_deletions")
    async def purge(ctx: Any, payload: dict[str, Any]) -> None:
        n = await compliance.run_due_deletions(ctx.session)
        if n:
            log.info("completed %s account deletions", n)

    reg.schedule("compliance-deletions", "every 1h", "compliance.run_due_deletions")

    if payments is not None:

        @reg.job("payments.issue_missing_invoices")
        async def invoices(ctx: Any, payload: dict[str, Any]) -> None:
            await payments.issue_missing_invoices(ctx.session)

        reg.schedule("payments-invoices", "every 15m", "payments.issue_missing_invoices")

    @reg.job("housekeeping")
    async def housekeeping(ctx: Any, payload: dict[str, Any]) -> None:
        """Retention (docs/legal/data-retention.md): finished jobs 14 days, webhook payloads 90 days, outbox 13 months."""
        s = ctx.session
        await s.execute(
            text(
                "DELETE FROM jobs WHERE status IN ('done', 'dead') AND finished_at < now() - interval '14 days'"
            )
        )
        await s.execute(
            text("DELETE FROM webhook_events WHERE received_at < now() - interval '90 days'")
        )
        await s.execute(text("DELETE FROM emails WHERE created_at < now() - interval '400 days'"))
        await s.execute(text("DELETE FROM events WHERE ts < now() - interval '400 days'"))
        # A signup nobody confirmed within 30 days never became consent to mail them: delete it.
        await s.execute(
            text(
                "DELETE FROM leads WHERE confirmed_at IS NULL AND created_at < now() - interval '30 days'"
            )
        )

    reg.schedule("housekeeping", "daily 03:15", "housekeeping")
    return reg
