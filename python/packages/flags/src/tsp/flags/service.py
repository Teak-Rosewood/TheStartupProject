from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record

from .models import Flag

KEY = re.compile(r"^[a-z0-9][a-z0-9_:.-]{0,63}$")


def bucket(key: str, who: str) -> int:
    """Stable 0-99 bucket for (flag, person): the same person always gets the same answer, and raising
    the percentage only adds people (everyone already in stays in)."""
    return int.from_bytes(hashlib.sha256(f"{key}:{who}".encode()).digest()[:4], "big") % 100


@dataclass(frozen=True)
class FlagContext:
    user: str | None = None
    org: str | None = None
    plan: str | None = None


@dataclass(frozen=True)
class FlagUpdate:
    enabled: bool | None = None
    rollout_percent: int | None = None
    rules: dict[str, Any] | None = None
    description: str | None = None
    client_visible: bool | None = None


@dataclass
class _Snapshot:
    at: float
    flags: dict[str, Flag]


class Flags:
    """Reads flags through a short in-process cache (default 30 s), so a flag check costs nothing.
    Changes made in this process show immediately; other processes catch up within the TTL."""

    def __init__(self, *, ttl: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.ttl, self._clock = ttl, clock
        self._snap: _Snapshot | None = None

    def invalidate(self) -> None:
        self._snap = None

    async def _all(self, session: AsyncSession) -> dict[str, Flag]:
        now = self._clock()
        if self._snap is None or now - self._snap.at > self.ttl:
            rows = (await session.execute(select(Flag))).scalars().all()
            for r in rows:
                session.expunge(r)  # a plain snapshot: no session needed to read it later
            self._snap = _Snapshot(now, {f.key: f for f in rows})
        return self._snap.flags

    @staticmethod
    def evaluate(flag: Flag | None, ctx: FlagContext, default: bool = False) -> bool:
        """Decide for one person. Order: master switch, deny lists, allow lists, plan, then rollout %.

        ``rollout_percent`` applies to everyone not decided earlier (100 = everyone, 0 = nobody), so a
        "Pro-only" feature is simply rollout 0 plus ``plans: ["pro"]``.
        """
        if flag is None:
            return default
        if not flag.enabled:
            return False
        rules = flag.rules or {}
        if (ctx.user and ctx.user in rules.get("deny_users", [])) or (
            ctx.org and ctx.org in rules.get("deny_orgs", [])
        ):
            return False
        if (ctx.user and ctx.user in rules.get("users", [])) or (
            ctx.org and ctx.org in rules.get("orgs", [])
        ):
            return True
        if ctx.plan and ctx.plan in rules.get("plans", []):
            return True
        pct = flag.rollout_percent
        if pct >= 100:
            return True
        who = ctx.org or ctx.user
        return pct > 0 and who is not None and bucket(flag.key, who) < pct

    async def enabled(
        self,
        session: AsyncSession,
        key: str,
        ctx: FlagContext | None = None,
        *,
        default: bool = False,
    ) -> bool:
        return self.evaluate((await self._all(session)).get(key), ctx or FlagContext(), default)

    async def killed(self, session: AsyncSession, name: str) -> bool:
        """True when the kill switch ``kill:<name>`` is switched on (the feature should refuse to run)."""
        flag = (await self._all(session)).get(f"kill:{name}")
        return bool(flag and flag.enabled)

    async def client_flags(self, session: AsyncSession, ctx: FlagContext) -> dict[str, bool]:
        """What a frontend may know: only flags marked ``client_visible``."""
        return {
            k: self.evaluate(f, ctx)
            for k, f in (await self._all(session)).items()
            if f.client_visible
        }

    async def set(
        self,
        session: AsyncSession,
        key: str,
        update: FlagUpdate,
        *,
        actor: Actor,
        reason: str,
        request: Any = None,
    ) -> Flag:
        if not KEY.match(key):
            raise ValueError(
                "flag keys are lowercase letters, digits and _ : . - (up to 64 characters)"
            )
        if update.rollout_percent is not None and not 0 <= update.rollout_percent <= 100:
            raise ValueError("rollout_percent must be between 0 and 100")
        values: dict[str, Any] = {
            k: v
            for k, v in {
                "enabled": update.enabled,
                "rollout_percent": update.rollout_percent,
                "rules": update.rules,
                "description": update.description,
                "client_visible": update.client_visible,
            }.items()
            if v is not None
        }
        values["updated_by"] = actor.sub
        stmt = insert(Flag).values(
            key=key, **{"rollout_percent": 100, **values}
        )  # a new flag defaults to "everyone"
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["key"], set_={**values, "updated_at": func.now()}
            )
        )
        await record(
            session,
            "flag.changed",
            actor=actor,
            target=("flag", key),
            request=request,
            reason=reason,
            change={k: v for k, v in values.items() if k != "updated_by"},
        )
        self.invalidate()
        flag = (await session.execute(select(Flag).where(Flag.key == key))).scalar_one()
        return flag
