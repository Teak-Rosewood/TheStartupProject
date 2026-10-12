from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .schedule import parse_schedule


@dataclass
class JobContext:
    """Passed to every handler. ``session`` commits when the handler returns, rolls back if it raises."""

    session: AsyncSession
    job_id: int
    attempt: int
    name: str
    database: Any = None  # the worker's Database, for handlers that manage their own sessions


Handler = Callable[[JobContext, dict[str, Any]], Awaitable[None]]


@dataclass
class JobRegistry:
    handlers: dict[str, Handler] = field(default_factory=dict)
    schedules: dict[str, tuple[str, str, dict[str, Any]]] = field(
        default_factory=dict
    )  # cron name -> (spec, job, payload)

    def job(self, name: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            if name in self.handlers:
                raise ValueError(f"job {name!r} is already registered")
            self.handlers[name] = fn
            return fn

        return deco

    def schedule(
        self, cron_name: str, spec: str, job_name: str, payload: dict[str, Any] | None = None
    ) -> None:
        """Run ``job_name`` on a schedule. Cron names are unique; re-registering replaces the schedule."""
        self.schedules[cron_name] = (parse_schedule(spec), job_name, payload or {})


async def enqueue(
    session: AsyncSession,
    name: str,
    payload: dict[str, Any] | None = None,
    *,
    run_at: datetime | None = None,
    unique_key: str | None = None,
    max_attempts: int = 5,
) -> int | None:
    """Queue a job in the caller's transaction (the job exists only if the surrounding change commits).

    With ``unique_key`` a second enqueue of the same key is ignored and returns None.
    """
    import json

    row = await session.execute(
        text(
            "INSERT INTO jobs (name, payload, run_at, max_attempts, unique_key) "
            "VALUES (:n, CAST(:p AS jsonb), COALESCE(:r, now()), :m, :u) "
            "ON CONFLICT (unique_key) DO NOTHING RETURNING id"
        ),
        {
            "n": name,
            "p": json.dumps(payload or {}),
            "r": run_at,
            "m": max_attempts,
            "u": unique_key,
        },
    )
    found = row.first()
    return int(found[0]) if found else None
