from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Credit, Gift, Read, Unlock


def register_with_compliance(compliance: Any) -> None:
    """Reading history is personal data: export it, and delete it with the account. Anonymous meters
    cannot be linked to a person and are not included."""

    async def export(session: AsyncSession, sub: str) -> dict[str, Any]:
        reads = (
            await session.execute(select(Read).where(Read.user_id == sub).order_by(Read.at))
        ).scalars()
        unlocks = (
            await session.execute(select(Unlock).where(Unlock.subject == f"u:{sub}"))
        ).scalars()
        credits = (await session.execute(select(Credit).where(Credit.user_id == sub))).scalars()
        return {
            "reads": [{"item": r.item_id, "at": r.at} for r in reads],
            "unlocked": [{"item": u.item_id, "via": u.via, "at": u.at} for u in unlocks],
            "credits": [{"org": c.org_id, "balance": c.balance} for c in credits],
        }

    async def erase(session: AsyncSession, sub: str) -> None:
        await session.execute(delete(Read).where(Read.user_id == sub))
        await session.execute(delete(Unlock).where(Unlock.subject == f"u:{sub}"))
        await session.execute(delete(Credit).where(Credit.user_id == sub))
        await session.execute(delete(Gift).where(Gift.giver_user_id == sub))

    compliance.register_exporter("paywall", export)
    compliance.register_eraser("paywall", erase)
