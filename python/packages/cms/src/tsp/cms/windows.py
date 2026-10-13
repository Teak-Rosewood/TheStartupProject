"""Restricted-mode windows: periods (an election, a court case) in which extra publishing rules apply.

Rules, per window: ``{"block_types": ["poll_results"], "require_check": ["window_review"]}``.
``block_types`` stops items of those types from publishing; ``require_check`` names attestations an
editor must record in the item's ``attrs["attested"]`` list before it can publish."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .checks import CheckContext, CheckResult
from .models import Item, Version, Window

FLAG_PREFIX = "cms.window."


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def flag_key(name: str) -> str:
    return FLAG_PREFIX + re.sub(r"[^a-z0-9_.-]+", "-", name.lower()).strip("-")[:48]


class Windows:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] = _utcnow):
        self.s, self.now = session, clock

    async def create(
        self,
        org_id: str,
        name: str,
        starts_at: datetime,
        ends_at: datetime,
        *,
        region: str | None = None,
        rules: dict[str, Any] | None = None,
    ) -> Window:
        if starts_at.tzinfo is None or ends_at.tzinfo is None:
            raise ValueError("window times need a timezone")
        if ends_at <= starts_at:
            raise ValueError("a window must end after it starts")
        w = Window(
            org_id=org_id,
            name=name,
            starts_at=starts_at,
            ends_at=ends_at,
            region=region,
            rules=rules or {},
        )
        self.s.add(w)
        await self.s.flush()
        return w

    async def delete(self, org_id: str, name: str) -> None:
        w = (
            await self.s.execute(select(Window).where(Window.org_id == org_id, Window.name == name))
        ).scalar_one_or_none()
        if w is not None:
            await self.s.delete(w)

    async def active(
        self, org_id: str, at: datetime | None = None, region: str | None = None
    ) -> list[Window]:
        """Windows running at ``at``: those with no region (everyone) and those for ``region``."""
        at = at or self.now()
        q = select(Window).where(
            Window.org_id == org_id,
            Window.starts_at <= at,
            Window.ends_at > at,
            or_(Window.region.is_(None), Window.region == region)
            if region
            else Window.region.is_(None),
        )
        return list((await self.s.execute(q.order_by(Window.starts_at))).scalars())


class WindowRules:
    """Publish check: while a window is open, its rules apply to every item of the organization."""

    name, blocking = "window_rules", True

    def __init__(self, clock: Callable[[], datetime] = _utcnow):
        self.clock = clock

    async def run(self, ctx: CheckContext, item: Item, version: Version) -> CheckResult:
        wins = await Windows(ctx.session, clock=self.clock).active(
            item.org_id, region=(item.attrs or {}).get("region")
        )
        blocked: list[str] = []
        missing: list[str] = []
        attested = set((item.attrs or {}).get("attested", []))
        for w in wins:
            rules = w.rules or {}
            if item.type in rules.get("block_types", []):
                blocked.append(w.name)
            missing += [c for c in rules.get("require_check", []) if c not in attested]
        ok = not blocked and not missing
        return CheckResult(
            ok,
            {
                "windows": [w.name for w in wins],
                "blocked_by": blocked,
                "missing_attestations": sorted(set(missing)),
            },
        )


async def sync_window_flags(
    session: AsyncSession, flags: Any, *, clock: Callable[[], datetime] = _utcnow
) -> list[str]:
    """Make the ``cms.window.<name>`` flag match whether the window is open, so other modules can
    react without knowing about the CMS. Flags of windows that no longer exist are switched off.
    Returns the keys it changed. Call it every minute."""
    from tsp.audit import Actor
    from tsp.flags import Flag, FlagUpdate

    now = clock()
    wanted: dict[str, bool] = {}
    for w in (await session.execute(select(Window))).scalars():
        key = flag_key(w.name)
        wanted[key] = wanted.get(key, False) or (w.starts_at <= now < w.ends_at)
    existing = {
        f.key: f.enabled
        for f in (
            await session.execute(select(Flag).where(Flag.key.like(FLAG_PREFIX + "%")))
        ).scalars()
    }
    for key in existing:
        wanted.setdefault(key, False)
    changed = []
    for key, on in wanted.items():
        if existing.get(key) != on:
            await flags.set(
                session, key, FlagUpdate(enabled=on, description="Set automatically from cms_windows"),
                actor=Actor("system", "system"), reason="window opened" if on else "window closed",
            )  # fmt: skip
            changed.append(key)
    return changed
