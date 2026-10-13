from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .config import EngageConfig
from .onboarding import Onboarding
from .points import Points
from .polls import Polls
from .receipts import Receipts
from .streaks import Streaks


@dataclass
class ReadOutcome:
    newly_read: bool = False
    points: int = 0
    streak_week_met: bool = False
    badges: list[str] = field(default_factory=list)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Engage:
    """One reader's engagement services on one session. Most apps use this instead of the parts."""

    def __init__(
        self,
        session: AsyncSession,
        cfg: EngageConfig | None = None,
        *,
        clock: Callable[[], datetime] = _utcnow,
        allow_ping: Callable[[str], Awaitable[bool]] | None = None,
    ):
        self.s, self.cfg, self.now = session, cfg or EngageConfig(), clock
        self.receipts = Receipts(session, clock=clock, allow=allow_ping)
        self.polls = Polls(session, clock=clock, min_k=self.cfg.min_k, bins=self.cfg.bins)
        self.streaks = Streaks(session, self.cfg, clock=clock)
        self.points = Points(session, self.cfg)
        self.onboarding = Onboarding(session, self.cfg)

    async def record_read(self, user_id: str, org_id: str, item_id: str) -> ReadOutcome:
        """Call after a ping. The first time the item counts as read it pays points, advances the
        streak and may grant badges; later calls do nothing."""
        out = ReadOutcome()
        if not await self.receipts.complete_if_read(user_id, item_id, self.cfg.read_policy):
            return out
        out.newly_read = True
        out.points += await self.points.award(
            user_id, org_id, "read", ref_kind="item", ref_id=item_id
        )
        state, met = await self.streaks.on_read(user_id, org_id)
        out.streak_week_met = met
        if met:
            out.points += await self.points.award(
                user_id, org_id, "streak_week", ref_kind="week", ref_id=state.week
            )
        out.badges = await self.points.check_badges(user_id, org_id, state.current)
        return out

    async def respond_to_poll(
        self, user_id: str, poll_id: Any, phase: str, x: float, y: float | None = None
    ) -> int:
        """Record an answer and pay the poll points once per poll (when both answers are in)."""
        await self.polls.respond(user_id, poll_id, phase, x, y)
        poll = await self.polls.get(poll_id)
        paid = 0
        if await self.polls.shift(user_id, poll_id) is not None:
            paid = await self.points.award(
                user_id, poll.org_id, "poll", ref_kind="poll", ref_id=str(poll_id)
            )
        return paid

    async def complete_onboarding(self, user_id: str, org_id: str, step: str) -> int:
        paid = 0
        if await self.onboarding.complete(user_id, org_id, step):
            paid = await self.points.award(
                user_id, org_id, "onboarding", ref_kind="step", ref_id=step
            )
        return paid

    async def me(self, user_id: str, org_id: str) -> dict[str, Any]:
        """The reader's private history. Never shown to anyone else."""
        st = await self.streaks.state(user_id, org_id)
        return {
            "polls": await self.polls.history(user_id),
            "streak": {"current": st.current, "best": st.best, "week": st.week, "progress": st.progress, "goal": st.goal, "met_this_week": st.met_this_week},
            "points": await self.points.balance(user_id, org_id),
            "badges": await self.points.badges(user_id, org_id),
            "onboarding": {"done": await self.onboarding.done(user_id, org_id), "remaining": await self.onboarding.remaining(user_id, org_id)},
            "reads": sum(1 for r in await self.receipts.history(user_id) if r.org_id == org_id and r.completed_at is not None),
        }  # fmt: skip
