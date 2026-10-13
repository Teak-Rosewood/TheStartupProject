from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from .config import EngageConfig
from .models import Streak


def _utcnow() -> datetime:
    return datetime.now(UTC)


def week_key(at: datetime, tz_name: str) -> str:
    """ISO week in the organization's time zone: ``2026-W41``. Weeks start on Monday."""
    from zoneinfo import ZoneInfo

    local = at.astimezone(ZoneInfo(tz_name))
    iso = local.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def previous_week_key(key: str) -> str:
    year, week = key.split("-W")
    monday = date.fromisocalendar(int(year), int(week), 1) - timedelta(days=7)
    iso = monday.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@dataclass(frozen=True)
class StreakState:
    current: int  # consecutive weeks that met the goal, counting this one once it does
    best: int
    week: str
    progress: int  # items read so far this week
    goal: int
    met_this_week: bool


class Streaks:
    """Weekly streaks: a week counts when the reader finished ``weekly_goal`` items in it. Missing a
    whole week resets ``current`` to zero but never lowers ``best``."""

    def __init__(
        self,
        session: AsyncSession,
        cfg: EngageConfig | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ):
        self.s, self.cfg, self.now = session, cfg or EngageConfig(), clock

    async def _row(self, user_id: str, org_id: str) -> Streak:
        row = await self.s.get(Streak, (user_id, org_id, "week"))
        if row is None:
            row = Streak(
                user_id=user_id, org_id=org_id, period="week", current=0, best=0, progress_count=0
            )
            self.s.add(row)
            await self.s.flush()
        return row

    async def on_read(
        self, user_id: str, org_id: str, at: datetime | None = None
    ) -> tuple[StreakState, bool]:
        """Count one finished item. Returns the state and whether this read completed the week's goal.
        The caller must call it once per item (``Receipts.complete_if_read`` guarantees that)."""
        at = at or self.now()
        key = week_key(at, self.cfg.timezone)
        row = await self._row(user_id, org_id)
        if row.progress_key != key:
            row.progress_key, row.progress_count = key, 0
        row.progress_count += 1
        reached = row.progress_count == self.cfg.weekly_goal
        if reached:
            row.current = row.current + 1 if row.last_period_key == previous_week_key(key) else 1
            row.best = max(row.best, row.current)
            row.last_period_key = key
        return self._state(row, key), reached

    def _state(self, row: Streak, key: str) -> StreakState:
        alive = row.last_period_key in (key, previous_week_key(key))
        progress = row.progress_count if row.progress_key == key else 0
        return StreakState(
            current=row.current if alive else 0, best=row.best, week=key,
            progress=progress, goal=self.cfg.weekly_goal, met_this_week=row.last_period_key == key,
        )  # fmt: skip

    async def state(self, user_id: str, org_id: str, at: datetime | None = None) -> StreakState:
        key = week_key(at or self.now(), self.cfg.timezone)
        row = await self.s.get(Streak, (user_id, org_id, "week"))
        if row is None:
            return StreakState(0, 0, key, 0, self.cfg.weekly_goal, False)
        return self._state(row, key)
