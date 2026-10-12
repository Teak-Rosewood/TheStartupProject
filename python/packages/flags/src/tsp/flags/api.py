from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .service import FlagContext, Flags


def build_router(
    flags: Flags,
    account: Callable[..., Any],
    session: Callable[..., Any],
    plan_of: Callable[..., Any] | None = None,
) -> APIRouter:
    """``GET /flags``: the flags a frontend may see, evaluated for the signed-in user.

    ``plan_of(session, user)`` optionally returns the plan code so plan-targeted flags work in the UI.
    """
    r = APIRouter()

    @r.get("/flags")
    async def mine(
        u: Any = Depends(account), s: AsyncSession = Depends(session)
    ) -> dict[str, bool]:
        plan = await plan_of(s, u) if plan_of else None
        return await flags.client_flags(s, FlagContext(user=u.sub, plan=plan))

    return r


def require_enabled(
    flags: Flags, key: str, session: Callable[..., Any], ctx_of: Callable[..., Any] | None = None
) -> Callable[..., Any]:
    """Dependency: 404 unless the flag is on (hides unreleased features instead of advertising them)."""

    async def dep(s: AsyncSession = Depends(session)) -> None:
        if not await flags.enabled(s, key):
            raise HTTPException(404, "not found")

    return dep


def require_not_killed(flags: Flags, name: str, session: Callable[..., Any]) -> Callable[..., Any]:
    """Dependency: 503 while the kill switch ``kill:<name>`` is on, so a broken feature can be stopped in seconds."""

    async def dep(s: AsyncSession = Depends(session)) -> None:
        if await flags.killed(s, name):
            raise HTTPException(
                503,
                detail={
                    "type": "feature_disabled",
                    "message": f"{name} is temporarily unavailable",
                    "feature": name,
                },
            )

    return dep
