from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Complaint, ComplaintEvent


def register_complaints_with_compliance(
    compliance: Any, email_of: Callable[[AsyncSession, str], Awaitable[str | None]]
) -> None:
    """Complainants are people who may or may not have an account, so records are matched by the
    account's email. Export lists what they sent and how it ended. Erasure keeps the facts the
    organization may need to show regulators (category, dates, level, outcome) but removes the name,
    email and message, and the free-text notes staff wrote about the person."""

    async def export(session: AsyncSession, sub: str) -> list[dict[str, Any]]:
        email = await email_of(session, sub)
        if not email:
            return []
        rows = (
            await session.execute(
                select(Complaint)
                .where(Complaint.email == email.lower())
                .order_by(Complaint.received_at)
            )
        ).scalars()
        return [
            {
                "reference": c.reference,
                "received_at": c.received_at,
                "category": c.category,
                "message": c.body,
                "status": c.status,
                "resolution": c.resolution,
            }  # fmt: skip
            for c in rows
        ]

    async def erase(session: AsyncSession, sub: str) -> None:
        email = await email_of(session, sub)
        if not email:
            return
        alias = "deleted:" + hashlib.sha256(email.lower().encode()).hexdigest()[:16]
        ids = list(
            (
                await session.execute(select(Complaint.id).where(Complaint.email == email.lower()))
            ).scalars()
        )
        if not ids:
            return
        await session.execute(
            update(Complaint)
            .where(Complaint.id.in_(ids))
            .values(complainant_name="[removed]", email=alias, body="[removed]")
        )
        await session.execute(
            update(ComplaintEvent)
            .where(
                ComplaintEvent.complaint_id.in_(ids),
                ComplaintEvent.kind.in_(("note", "acknowledged")),
            )
            .values(note="[removed]")
        )

    compliance.register_exporter("complaints", export)
    compliance.register_eraser("complaints", erase)
