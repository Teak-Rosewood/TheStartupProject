from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Submission

PURGE = "engage.purge_rejected"


async def purge_rejected(
    session: AsyncSession, *, keep_days: int = 90, now: datetime | None = None
) -> int:
    """Delete rejected takes older than ``keep_days`` (the retention schedule). Appealed takes are kept."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    res = await session.execute(
        delete(Submission).where(Submission.status == "rejected", Submission.decided_at < cutoff)
    )
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


def register_jobs(registry: Any, *, keep_days: int = 90) -> None:
    """A daily job that applies the retention schedule to rejected takes."""

    @registry.job(PURGE)
    async def purge(ctx: Any, payload: dict[str, Any]) -> None:
        await purge_rejected(ctx.session, keep_days=keep_days)

    registry.schedule("engage-purge", "daily 03:50", PURGE)
