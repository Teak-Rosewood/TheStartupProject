"""Join the data-protection machinery: include notification data in exports, remove it on deletion."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DeviceToken, Email, EmailPreference, Notification, Suppression


def register(
    compliance: Any, email_of: Callable[[AsyncSession, str], Awaitable[str | None]]
) -> None:
    """``email_of(session, sub)`` returns the user's email (or None). Call before the user is erased."""

    async def export(session: AsyncSession, sub: str) -> dict[str, Any]:
        email = await email_of(session, sub)
        notes = (
            (await session.execute(select(Notification).where(Notification.user_sub == sub)))
            .scalars()
            .all()
        )
        devices = (
            (await session.execute(select(DeviceToken).where(DeviceToken.user_sub == sub)))
            .scalars()
            .all()
        )
        out: dict[str, Any] = {
            "notifications": [
                {"kind": n.kind, "title": n.title, "created_at": n.created_at, "read_at": n.read_at}
                for n in notes
            ],
            "devices": [{"platform": d.platform, "registered_at": d.created_at} for d in devices],
        }
        if email:
            pref = await session.get(EmailPreference, email.lower())
            mails = (
                (
                    await session.execute(
                        select(Email)
                        .where(Email.to_addr == email.lower())
                        .order_by(Email.created_at)
                    )
                )
                .scalars()
                .all()
            )
            out["email"] = {
                "marketing_consent": bool(pref and pref.marketing),
                "messages": [
                    {
                        "template": m.template,
                        "category": m.category,
                        "status": m.status,
                        "sent_at": m.sent_at,
                    }
                    for m in mails
                ],
            }
        return out

    async def erase(session: AsyncSession, sub: str) -> None:
        email = await email_of(session, sub)
        await session.execute(delete(Notification).where(Notification.user_sub == sub))
        await session.execute(delete(DeviceToken).where(DeviceToken.user_sub == sub))
        if email:
            addr = email.lower()
            alias = "deleted:" + hashlib.sha256(addr.encode()).hexdigest()[:16]
            # Keep that a message was sent (bounce history, delivery proof), not to whom or with what content.
            await session.execute(
                update(Email).where(Email.to_addr == addr).values(to_addr=alias, context={})
            )
            await session.execute(delete(EmailPreference).where(EmailPreference.email == addr))
            # The suppression row stays: forgetting a bounce or complaint would let us mail them again.
            _ = Suppression

    compliance.register_exporter("notifications", export)
    compliance.register_eraser("notifications", erase)
