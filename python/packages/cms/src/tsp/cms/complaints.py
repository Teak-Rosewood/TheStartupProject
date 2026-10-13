"""The complaints desk: intake, acknowledgement, deadlines, escalation and a monthly report.

Deadlines and who handles each level are configuration; the defaults are neutral placeholders and the
app sets the legal values that apply to it."""

from __future__ import annotations

import csv
import io
import statistics
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record

from .models import Complaint, ComplaintEvent
from .service import SYSTEM, CmsError, CmsService

OPEN = ("received", "acknowledged", "in_progress", "escalated")
CLOSED = ("resolved", "rejected")
RECEIVED_TEMPLATE, UPDATE_TEMPLATE = "complaint_received", "complaint_update"


@dataclass(frozen=True)
class Level:
    handler: str  # who the complaint is assigned to at this level (a user id or a desk name)
    resolve_days: int = 30  # how long this level has to resolve it


@dataclass(frozen=True)
class ComplaintsConfig:
    """``ack_hours``: a person must acknowledge within this long. ``levels``: up to three, each with a
    handler and the time it has; a missed deadline moves the complaint to the next. Warnings go to
    staff ``warn_ack_hours`` / ``warn_resolve_days`` before a deadline."""

    ack_hours: int = 72
    warn_ack_hours: int = 12
    warn_resolve_days: int = 3
    levels: tuple[Level, ...] = (
        Level("support-desk", 30),
        Level("editor-in-chief", 15),
        Level("ombudsman", 15),
    )
    categories: tuple[str, ...] = ("accuracy", "privacy", "copyright", "harmful", "other")

    def __post_init__(self) -> None:
        if not 1 <= len(self.levels) <= 3:
            raise ValueError("a complaint has one to three escalation levels")


class Mailer(Protocol):
    """``tsp.notify.Notifier`` fits this."""

    async def queue(
        self,
        session: AsyncSession,
        to: str,
        template: str,
        ctx: dict[str, Any],
        *,
        category: str = ...,
    ) -> str: ...


StaffNotice = Callable[[AsyncSession, Complaint, str], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ComplaintsDesk:
    def __init__(
        self,
        session: AsyncSession,
        *,
        config: ComplaintsConfig | Callable[[str], ComplaintsConfig] | None = None,
        clock: Callable[[], datetime] = _utcnow,
        mailer: Mailer | None = None,
        notify_staff: StaffNotice | None = None,
        cms: CmsService | None = None,
    ):
        self.s = session
        self._config = config or ComplaintsConfig()
        self.now = clock
        self.mailer = mailer
        self.notify_staff = notify_staff
        self.cms = cms or CmsService(session, clock=clock)

    def config(self, org_id: str) -> ComplaintsConfig:
        return self._config(org_id) if callable(self._config) else self._config

    # -- helpers ---------------------------------------------------------------------------------

    async def _event(self, c: Complaint, actor: str, kind: str, note: str = "") -> None:
        self.s.add(
            ComplaintEvent(complaint_id=c.id, actor=actor, kind=kind, note=note, at=self.now())
        )

    async def _audit(self, c: Complaint, action: str, actor: Actor, **meta: Any) -> None:
        await record(
            self.s, f"cms.complaint.{action}", actor=actor, target=("cms_complaint", str(c.id)),
            org_id=c.org_id, reference=c.reference, **meta,
        )  # fmt: skip

    async def _tell_staff(self, c: Complaint, kind: str) -> None:
        if self.notify_staff:
            await self.notify_staff(self.s, c, kind)

    async def _mail(self, c: Complaint, template: str, **ctx: Any) -> None:
        if self.mailer:
            await self.mailer.queue(
                self.s, c.email, template,
                {"name": c.complainant_name, "reference": c.reference, **ctx},
            )  # fmt: skip

    async def get(self, org_id: str, complaint_id: uuid.UUID) -> Complaint:
        c = await self.s.get(Complaint, complaint_id)
        if c is None or c.org_id != org_id:
            raise LookupError("no such complaint")
        return c

    async def by_reference(self, org_id: str, reference: str) -> Complaint:
        rows = (await self.s.execute(select(Complaint).where(Complaint.org_id == org_id))).scalars()
        for c in rows:
            if c.reference == reference:
                return c
        raise LookupError("no such complaint")

    async def events(self, org_id: str, complaint_id: uuid.UUID) -> list[ComplaintEvent]:
        await self.get(org_id, complaint_id)
        return list(
            (
                await self.s.execute(
                    select(ComplaintEvent)
                    .where(ComplaintEvent.complaint_id == complaint_id)
                    .order_by(ComplaintEvent.at, ComplaintEvent.id)
                )
            ).scalars()
        )

    async def listing(
        self, org_id: str, *, status: str | None = None, level: int | None = None, limit: int = 200
    ) -> list[Complaint]:
        q = select(Complaint).where(Complaint.org_id == org_id)
        if status == "open":
            q = q.where(Complaint.status.in_(OPEN))
        elif status:
            q = q.where(Complaint.status == status)
        if level:
            q = q.where(Complaint.level == level)
        q = q.order_by(Complaint.received_at.desc(), Complaint.seq.desc()).limit(min(limit, 500))
        return list((await self.s.execute(q)).scalars())

    # -- intake ----------------------------------------------------------------------------------

    async def intake(
        self,
        org_id: str,
        name: str,
        email: str,
        category: str,
        body: str,
        *,
        item_slug: str | None = None,
    ) -> Complaint:
        cfg = self.config(org_id)
        name, email, body = name.strip(), email.strip().lower(), body.strip()
        if not name or len(name) > 200:
            raise CmsError("give your name (up to 200 characters)")
        if "@" not in email or len(email) > 320 or " " in email:
            raise CmsError("give a valid email address")
        if category not in cfg.categories:
            raise CmsError(f"category must be one of: {', '.join(cfg.categories)}")
        if not 10 <= len(body) <= 5000:
            raise CmsError("describe the problem in 10 to 5000 characters")
        item_id = None
        if item_slug:
            try:
                item_id = (await self.cms.by_slug(org_id, item_slug)).id
            except LookupError as e:
                raise CmsError("we could not find that page") from e
        now = self.now()
        first = cfg.levels[0]
        c = Complaint(
            org_id=org_id, item_id=item_id, complainant_name=name, email=email, category=category,
            body=body, received_at=now, ack_due_at=now + timedelta(hours=cfg.ack_hours),
            resolve_due_at=now + timedelta(days=first.resolve_days), assigned_to=first.handler,
        )  # fmt: skip
        self.s.add(c)
        await self.s.flush()
        await self._event(c, "complainant", "received", category)
        await self._audit(c, "received", Actor(None, "public"), category=category)
        await self._mail(c, RECEIVED_TEMPLATE, ack_hours=cfg.ack_hours)
        await self._tell_staff(c, "received")
        return c

    # -- handling --------------------------------------------------------------------------------

    def _open(self, c: Complaint) -> None:
        if c.status in CLOSED:
            raise CmsError(f"the complaint is already {c.status}")

    async def acknowledge(
        self, org_id: str, complaint_id: uuid.UUID, actor: Actor, message: str = ""
    ) -> Complaint:
        c = await self.get(org_id, complaint_id)
        self._open(c)
        if c.acknowledged_at is None:
            c.acknowledged_at, c.ack_due_at = self.now(), None
            if c.status == "received":
                c.status = "acknowledged"
            await self._event(c, str(actor.sub), "acknowledged", message)
            await self._audit(c, "acknowledge", actor)
            if message.strip():
                await self._mail(c, UPDATE_TEMPLATE, message=message.strip(), final=False)
        return c

    async def assign(
        self, org_id: str, complaint_id: uuid.UUID, assignee: str, actor: Actor
    ) -> Complaint:
        c = await self.get(org_id, complaint_id)
        self._open(c)
        c.assigned_to = assignee
        await self._event(c, str(actor.sub), "assigned", assignee)
        await self._audit(c, "assign", actor, to=assignee)
        return c

    async def start(self, org_id: str, complaint_id: uuid.UUID, actor: Actor) -> Complaint:
        c = await self.get(org_id, complaint_id)
        self._open(c)
        if c.acknowledged_at is None:
            await self.acknowledge(org_id, complaint_id, actor)
        c.status = "in_progress"
        await self._event(c, str(actor.sub), "in_progress")
        return c

    async def note(
        self, org_id: str, complaint_id: uuid.UUID, actor: Actor, note: str
    ) -> Complaint:
        c = await self.get(org_id, complaint_id)
        if not note.strip():
            raise CmsError("a note cannot be empty")
        await self._event(c, str(actor.sub), "note", note.strip())
        return c

    async def resolve(
        self,
        org_id: str,
        complaint_id: uuid.UUID,
        actor: Actor,
        resolution: str,
        *,
        correction: tuple[str, str] | None = None,
        correction_id: uuid.UUID | None = None,
    ) -> Complaint:
        """Close it with an explanation. ``correction=(kind, public_note)`` adds a public log entry to
        the item the complaint is about and links it; ``correction_id`` links one that already exists
        (for example from a republish that passed this complaint's id)."""
        c = await self.get(org_id, complaint_id)
        self._open(c)
        if not resolution.strip():
            raise CmsError("say what was decided")
        if correction is not None:
            if c.item_id is None:
                raise CmsError("this complaint is not about a page, so there is nothing to correct")
            entry = await self.cms.add_note(
                org_id, c.item_id, correction[0], correction[1], actor,
                internal_note=f"complaint {c.reference}", complaint_id=c.id,
            )  # fmt: skip
            c.correction_id = entry.id
        elif correction_id is not None:
            c.correction_id = correction_id
        c.status, c.resolution, c.closed_at = "resolved", resolution.strip(), self.now()
        c.ack_due_at = c.resolve_due_at = None
        await self._event(c, str(actor.sub), "resolved", resolution.strip())
        await self._audit(
            c, "resolve", actor, correction=str(c.correction_id) if c.correction_id else None
        )
        await self._mail(c, UPDATE_TEMPLATE, message=resolution.strip(), final=True)
        return c

    async def reject(
        self, org_id: str, complaint_id: uuid.UUID, actor: Actor, reason: str
    ) -> Complaint:
        c = await self.get(org_id, complaint_id)
        self._open(c)
        if not reason.strip():
            raise CmsError("say why it is rejected")
        c.status, c.resolution, c.closed_at = "rejected", reason.strip(), self.now()
        c.ack_due_at = c.resolve_due_at = None
        await self._event(c, str(actor.sub), "rejected", reason.strip())
        await self._audit(c, "reject", actor)
        await self._mail(c, UPDATE_TEMPLATE, message=reason.strip(), final=True)
        return c

    async def escalate(
        self, org_id: str, complaint_id: uuid.UUID, actor: Actor, reason: str
    ) -> Complaint:
        c = await self.get(org_id, complaint_id)
        self._open(c)
        await self._escalate(c, actor, reason)
        return c

    async def _escalate(self, c: Complaint, actor: Actor, reason: str) -> bool:
        cfg = self.config(c.org_id)
        if c.level >= len(cfg.levels):
            return False
        now = self.now()
        c.level += 1
        lvl = cfg.levels[c.level - 1]
        c.assigned_to, c.status = lvl.handler, "escalated"
        c.resolve_due_at = now + timedelta(days=lvl.resolve_days)
        c.ack_due_at = None if c.acknowledged_at else now + timedelta(hours=cfg.ack_hours)
        c.ack_warned_at = c.resolve_warned_at = None
        await self._event(c, str(actor.sub), "escalated", f"level {c.level}: {reason}")
        await self._audit(c, "escalate", actor, level=c.level, reason=reason)
        await self._tell_staff(c, "escalated")
        return True

    # -- deadlines (hourly job) ----------------------------------------------------------------------

    async def run_sla(self, system: Actor = SYSTEM) -> dict[str, list[uuid.UUID]]:
        """Warn staff before a deadline and escalate when it is missed. Each warning goes out once per
        deadline; a complaint already at the last level is flagged and given a week at a time."""
        now = self.now()
        warned: list[uuid.UUID] = []
        escalated: list[uuid.UUID] = []
        open_ = (
            (
                await self.s.execute(
                    select(Complaint)
                    .where(Complaint.status.in_(OPEN))
                    .order_by(Complaint.received_at)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for c in open_:
            cfg = self.config(c.org_id)
            breached = None
            if c.acknowledged_at is None and c.ack_due_at is not None:
                if now >= c.ack_due_at:
                    breached = "acknowledgement deadline missed"
                elif (
                    now >= c.ack_due_at - timedelta(hours=cfg.warn_ack_hours)
                    and c.ack_warned_at is None
                ):
                    c.ack_warned_at = now
                    await self._event(
                        c, system.sub or "system", "warning", "acknowledgement due soon"
                    )
                    await self._tell_staff(c, "warn_ack")
                    warned.append(c.id)
            if breached is None and c.resolve_due_at is not None:
                if now >= c.resolve_due_at:
                    breached = "resolution deadline missed"
                elif (
                    now >= c.resolve_due_at - timedelta(days=cfg.warn_resolve_days)
                    and c.resolve_warned_at is None
                ):
                    c.resolve_warned_at = now
                    await self._event(c, system.sub or "system", "warning", "resolution due soon")
                    await self._tell_staff(c, "warn_resolve")
                    warned.append(c.id)
            if breached:
                if await self._escalate(c, system, breached):
                    escalated.append(c.id)
                else:  # nowhere left to go: say so, and look again in a week
                    c.resolve_due_at = now + timedelta(days=7)
                    c.ack_due_at = None if c.acknowledged_at else now + timedelta(days=7)
                    await self._event(
                        c, system.sub or "system", "breach", f"{breached} at the last level"
                    )
                    await self._tell_staff(c, "breach")
        await self.s.flush()
        return {"warned": warned, "escalated": escalated}

    # -- reporting -----------------------------------------------------------------------------------

    async def _month(self, org_id: str, month: str) -> list[Complaint]:
        try:
            start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
        except ValueError as e:
            raise CmsError("month must look like 2026-03") from e
        end = (start + timedelta(days=32)).replace(day=1)
        return list(
            (
                await self.s.execute(
                    select(Complaint)
                    .where(
                        Complaint.org_id == org_id,
                        Complaint.received_at >= start,
                        Complaint.received_at < end,
                    )
                    .order_by(Complaint.received_at, Complaint.seq)
                )
            ).scalars()
        )

    @staticmethod
    def _hours(a: datetime | None, b: datetime) -> float | None:
        return round((a - b).total_seconds() / 3600, 1) if a else None

    async def report(self, org_id: str, month: str) -> dict[str, Any]:
        rows = await self._month(org_id, month)
        by_cat: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for c in rows:
            by_cat[c.category] = by_cat.get(c.category, 0) + 1
            by_status[c.status] = by_status.get(c.status, 0) + 1
        ack = [h for c in rows if (h := self._hours(c.acknowledged_at, c.received_at)) is not None]
        res = [h / 24 for c in rows if (h := self._hours(c.closed_at, c.received_at)) is not None]
        return {
            "month": month,
            "total": len(rows),
            "by_category": dict(sorted(by_cat.items())),
            "by_status": dict(sorted(by_status.items())),
            "escalated": sum(1 for c in rows if c.level > 1),
            "median_hours_to_acknowledge": round(statistics.median(ack), 1) if ack else None,
            "median_days_to_resolve": round(statistics.median(res), 1) if res else None,
        }

    async def report_csv(self, org_id: str, month: str) -> str:
        """One row per complaint, without names, emails or text: safe to share with an auditor."""
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(
            [
                "reference",
                "received_at",
                "category",
                "status",
                "level",
                "hours_to_acknowledge",
                "days_to_close",
                "linked_correction",
            ]
        )
        for c in await self._month(org_id, month):
            d = self._hours(c.closed_at, c.received_at)
            w.writerow([
                c.reference, c.received_at.isoformat(), c.category, c.status, c.level,
                self._hours(c.acknowledged_at, c.received_at) or "", round(d / 24, 1) if d is not None else "",
                str(c.correction_id) if c.correction_id else "",
            ])  # fmt: skip
        return out.getvalue()
