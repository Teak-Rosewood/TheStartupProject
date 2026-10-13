from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import BadgeRule, EngageConfig
from .models import Badge, Point


class Points:
    """Points are a log, paid once per (reason, thing). Amounts come from ``EngageConfig.points``."""

    def __init__(self, session: AsyncSession, cfg: EngageConfig | None = None):
        self.s, self.cfg = session, cfg or EngageConfig()

    async def award(
        self,
        user_id: str,
        org_id: str,
        reason: str,
        *,
        ref_kind: str = "",
        ref_id: str = "",
        points: int | None = None,
    ) -> int:
        """Points paid now (0 when this reason was already paid for this thing, or is worth nothing)."""
        amount = self.cfg.points.get(reason, 0) if points is None else points
        if amount <= 0:
            return 0
        res = await self.s.execute(
            insert(Point)
            .values(
                user_id=user_id,
                org_id=org_id,
                reason=reason,
                points=amount,
                ref_kind=ref_kind,
                ref_id=ref_id,
            )
            .on_conflict_do_nothing(constraint="uq_en_points_ref")
            .returning(Point.id)
        )
        return amount if res.first() else 0

    async def balance(self, user_id: str, org_id: str) -> int:
        return int(
            (
                await self.s.execute(
                    select(func.coalesce(func.sum(Point.points), 0)).where(
                        Point.user_id == user_id, Point.org_id == org_id
                    )
                )
            ).scalar_one()
        )

    async def count(self, user_id: str, org_id: str, reason: str) -> int:
        return int(
            (
                await self.s.execute(
                    select(func.count())
                    .select_from(Point)
                    .where(Point.user_id == user_id, Point.org_id == org_id, Point.reason == reason)
                )
            ).scalar_one()
        )

    async def badges(self, user_id: str, org_id: str) -> list[str]:
        rows = await self.s.execute(
            select(Badge.badge)
            .where(Badge.user_id == user_id, Badge.org_id == org_id)
            .order_by(Badge.at, Badge.badge)
        )
        return list(rows.scalars())

    async def check_badges(self, user_id: str, org_id: str, current_streak: int) -> list[str]:
        """Grant any badge whose rule is now met. Returns the newly granted ones."""
        have = set(await self.badges(user_id, org_id))
        new: list[str] = []
        for rule in self.cfg.badges:
            if rule.name in have or not await self._met(rule, user_id, org_id, current_streak):
                continue
            res = await self.s.execute(
                insert(Badge)
                .values(user_id=user_id, org_id=org_id, badge=rule.name)
                .on_conflict_do_nothing()
                .returning(Badge.badge)
            )
            if res.first():
                new.append(rule.name)
        return new

    async def _met(self, rule: BadgeRule, user_id: str, org_id: str, streak: int) -> bool:
        if rule.streak is not None:
            return streak >= rule.streak
        if rule.reason is not None and rule.count is not None:
            return await self.count(user_id, org_id, rule.reason) >= rule.count
        return False
