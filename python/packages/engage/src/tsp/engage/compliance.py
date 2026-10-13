from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Badge, OnboardingStep, Point, Receipt, Response, Streak


def register_with_compliance(compliance: Any) -> None:
    """Everything a reader did here is theirs to export and to have erased with the account."""

    async def export(session: AsyncSession, sub: str) -> dict[str, Any]:
        def rows(model: Any, *cols: str) -> Any:
            return session.execute(select(model).where(model.user_id == sub))

        return {
            "poll_responses": [{"poll": str(r.poll_id), "phase": r.phase, "x": r.x, "y": r.y, "at": r.at} for r in (await rows(Response)).scalars()],
            "reads": [{"item": r.item_id, "started_at": r.started_at, "completed_at": r.completed_at, "dwell_seconds": r.dwell_seconds} for r in (await rows(Receipt)).scalars()],
            "streaks": [{"org": r.org_id, "current": r.current, "best": r.best} for r in (await rows(Streak)).scalars()],
            "points": [{"org": r.org_id, "reason": r.reason, "points": r.points, "at": r.at} for r in (await rows(Point)).scalars()],
            "badges": [{"org": r.org_id, "badge": r.badge, "at": r.at} for r in (await rows(Badge)).scalars()],
            "onboarding": [{"org": r.org_id, "step": r.step, "at": r.completed_at} for r in (await rows(OnboardingStep)).scalars()],
        }  # fmt: skip

    async def erase(session: AsyncSession, sub: str) -> None:
        for model in (Response, Receipt, Streak, Point, Badge, OnboardingStep):
            await session.execute(delete(model).where(model.user_id == sub))

    compliance.register_exporter("engage", export)
    compliance.register_eraser("engage", erase)
