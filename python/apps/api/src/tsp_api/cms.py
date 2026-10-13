"""Wire the editorial backend into the app: settings, staff roles, reminders and the complaints desk."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.accounts import Accounts
from tsp.cms import (
    CmsService,
    ComplaintsConfig,
    ComplaintsDesk,
    Level,
    LifecycleConfig,
    Principal,
    WindowRules,
    default_registry,
)
from tsp.cms.models import Complaint, Item
from tsp.notify import Notifier

HEADLINES = {
    "received": "new complaint",
    "warn_ack": "acknowledgement due soon",
    "warn_resolve": "resolution due soon",
    "escalated": "escalated",
    "breach": "overdue at the last level",
}


@dataclass
class CmsSettings:
    public_org: str | None = None  # the organization whose corrections and complaints are public
    editors: list[str] = field(
        default_factory=list
    )  # who gets review reminders and complaint notices
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)
    complaints: ComplaintsConfig = field(default_factory=ComplaintsConfig)


def _levels(spec: str) -> tuple[Level, ...]:
    """``desk:30,chief:15,ombudsman:15`` -> handlers with their resolve days."""
    out = []
    for part in filter(None, (p.strip() for p in spec.split(","))):
        handler, _, days = part.partition(":")
        out.append(Level(handler.strip(), int(days) if days else 30))
    return tuple(out)


def _with_appeals(categories: tuple[str, ...]) -> tuple[str, ...]:
    """Rejected takes are appealed as complaints of this category (see tsp.engage)."""
    return categories if "moderation_appeal" in categories else (*categories, "moderation_appeal")


def cms_settings(env: Mapping[str, str] | None = None) -> CmsSettings:
    """Everything is optional; the defaults are neutral. Set the real deadlines your rules require."""
    e = os.environ if env is None else env
    lifecycle = LifecycleConfig(
        review_after_days=int(e.get("CMS_REVIEW_AFTER_DAYS", "30")),
        auto_freeze_after_days=int(e.get("CMS_AUTO_FREEZE_AFTER_DAYS", "7")),
    )
    base = ComplaintsConfig()
    complaints = ComplaintsConfig(
        ack_hours=int(e.get("CMS_COMPLAINT_ACK_HOURS", str(base.ack_hours))),
        warn_ack_hours=base.warn_ack_hours,
        warn_resolve_days=base.warn_resolve_days,
        levels=_levels(e["CMS_COMPLAINT_LEVELS"]) if e.get("CMS_COMPLAINT_LEVELS") else base.levels,
        categories=_with_appeals(
            tuple(c.strip() for c in e["CMS_COMPLAINT_CATEGORIES"].split(",") if c.strip())
            if e.get("CMS_COMPLAINT_CATEGORIES")
            else base.categories
        ),
    )
    return CmsSettings(
        public_org=e.get("CMS_PUBLIC_ORG") or None,
        editors=[x.strip() for x in e.get("CMS_EDITOR_EMAILS", "").split(",") if x.strip()],
        lifecycle=lifecycle,
        complaints=complaints,
    )


class Cms:
    """The pieces the app mounts, built once from settings and the notifier."""

    def __init__(self, settings: CmsSettings, notifier: Notifier):
        self.settings, self.notifier = settings, notifier
        self.checks = default_registry()
        self.checks.register("*", WindowRules())

    async def notify_review_due(
        self, session: AsyncSession, item: Item, deadline: datetime
    ) -> None:
        for to in self.settings.editors:
            await self.notifier.queue(
                session,
                to,
                "review_due",
                {"title": item.slug, "deadline": deadline.strftime("%d %B %Y")},
            )

    async def notify_staff(self, session: AsyncSession, c: Complaint, kind: str) -> None:
        due = c.ack_due_at if c.acknowledged_at is None and c.ack_due_at else c.resolve_due_at
        for to in self.settings.editors:
            await self.notifier.queue(
                session, to, "complaint_staff",
                {"reference": c.reference, "category": c.category, "level": c.level,
                 "headline": HEADLINES.get(kind, kind), "assigned_to": c.assigned_to or "nobody",
                 "due": due.strftime("%d %B %Y %H:%M UTC") if due else ""},
            )  # fmt: skip

    def service(self, session: AsyncSession) -> CmsService:
        return CmsService(
            session,
            checks=self.checks,
            lifecycle=self.settings.lifecycle,
            notify_review_due=self.notify_review_due,
        )

    def desk(self, session: AsyncSession) -> ComplaintsDesk:
        return ComplaintsDesk(
            session,
            config=self.settings.complaints,
            mailer=self.notifier,
            notify_staff=self.notify_staff,
            cms=self.service(session),
        )


def principal_dependency(accounts: Accounts, account: Any, session: Any) -> Any:
    """Staff roles come from organization membership: owners and admins edit and review, members
    edit. With several organizations, send ``X-Org-Id``."""

    async def principal(
        u: Any = Depends(account),
        s: AsyncSession = Depends(session),
        x_org_id: str | None = Header(default=None),
    ) -> Principal:
        orgs = await accounts.list_orgs(s, u.sub)
        if x_org_id:
            orgs = [(o, r) for o, r in orgs if o.id == x_org_id]
        if len(orgs) != 1:
            raise HTTPException(
                400 if orgs else 403,
                "send X-Org-Id: you belong to several organizations"
                if orgs
                else "you are not a member of that organization",
            )
        org, role = orgs[0]
        roles = {"editor", "reviewer"} if role in ("owner", "admin") else {"editor"}
        return Principal(org.id, u.sub, frozenset(roles))

    return principal
