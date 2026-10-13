from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import ReadPolicy
from .models import Receipt


class TooManyPings(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Receipts:
    """Proof that someone read an item, not just opened it. ``start`` is called by the server when
    the full item is sent (never for a paywalled preview); the page then sends ``ping`` beacons."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] = _utcnow,
        allow: Callable[[str], Awaitable[bool]] | None = None,
    ):
        self.s, self.now, self.allow = session, clock, allow

    async def start(
        self,
        user_id: str,
        org_id: str,
        item_id: str,
        version_id: str | None = None,
        *,
        served_full: bool = True,
        expected_seconds: int | None = None,
    ) -> Receipt:
        """Opening an item again keeps the earlier progress; a new version of it starts over."""
        existing = await self.s.get(Receipt, (user_id, item_id))
        if existing is not None and existing.version_id == version_id:
            existing.served_full = existing.served_full or served_full
            return existing
        await self.s.execute(
            insert(Receipt)
            .values(
                user_id=user_id, item_id=item_id, org_id=org_id, version_id=version_id,
                started_at=self.now(), expected_seconds=expected_seconds, served_full=served_full,
            )
            .on_conflict_do_update(
                index_elements=["user_id", "item_id"],
                set_={
                    "version_id": version_id, "started_at": self.now(), "completed_at": None,
                    "dwell_seconds": 0, "max_scroll": 0.0, "reached_end": False,
                    "served_full": served_full, "expected_seconds": expected_seconds,
                },
            )
        )  # fmt: skip
        await self.s.flush()
        row = await self.s.get(Receipt, (user_id, item_id), populate_existing=True)
        assert row is not None
        return row

    async def ping(
        self, user_id: str, item_id: str, dwell: float, scroll: float, reached_end: bool
    ) -> Receipt:
        """Record a beacon. Progress only moves forward, and dwell cannot exceed the time since the item was served."""
        if self.allow is not None and not await self.allow(f"{user_id}:{item_id}"):
            raise TooManyPings(item_id)
        r = await self.s.get(Receipt, (user_id, item_id))
        if r is None:
            raise LookupError("the item was not served to this reader")
        elapsed = max(0, int((self.now() - r.started_at).total_seconds()))
        r.dwell_seconds = max(r.dwell_seconds, min(int(max(dwell, 0)), elapsed))
        r.max_scroll = max(r.max_scroll, min(max(scroll, 0.0), 1.0))
        r.reached_end = r.reached_end or bool(reached_end)
        return r

    @staticmethod
    def satisfied(r: Receipt | None, policy: ReadPolicy) -> bool:
        if r is None or not r.served_full:
            return False
        expected = r.expected_seconds or policy.default_expected_seconds
        if r.dwell_seconds < policy.min_dwell_ratio * expected:
            return False
        return r.reached_end or not policy.require_end

    async def is_read(self, user_id: str, item_id: str, policy: ReadPolicy | None = None) -> bool:
        r = await self.s.get(Receipt, (user_id, item_id))
        return self.satisfied(r, policy or ReadPolicy())

    async def complete_if_read(self, user_id: str, item_id: str, policy: ReadPolicy) -> bool:
        """True exactly once: the first time the receipt meets the policy. Callers award streaks and points then."""
        r = await self.s.get(Receipt, (user_id, item_id))
        if r is None or r.completed_at is not None or not self.satisfied(r, policy):
            return False
        r.completed_at = self.now()
        return True

    async def history(self, user_id: str, limit: int = 100) -> list[Receipt]:
        rows = await self.s.execute(
            select(Receipt)
            .where(Receipt.user_id == user_id)
            .order_by(Receipt.started_at.desc())
            .limit(limit)
        )
        return list(rows.scalars())
