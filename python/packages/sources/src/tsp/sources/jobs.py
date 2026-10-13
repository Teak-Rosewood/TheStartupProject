from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from tsp.jobs import JobContext, JobRegistry, enqueue, next_run

from .checks import SourceChecks
from .models import Run, Source
from .registry import get_connector
from .runner import SourceRunner

log = logging.getLogger(__name__)


def register_jobs(registry: JobRegistry, runner: SourceRunner, checks: SourceChecks) -> None:
    """``sources.dispatch`` runs every 5 minutes and enqueues ``sources.sync`` for each enabled source
    whose own ``schedule`` has come due; ``sources.check`` and ``sources.reconcile`` run weekly."""

    @registry.job("sources.sync")
    async def sync(ctx: JobContext, payload: dict[str, Any]) -> None:
        await runner.sync(payload["source_key"], payload.get("stream"), payload.get("since"))

    @registry.job("sources.check")
    async def check(ctx: JobContext, payload: dict[str, Any]) -> None:
        if "source_key" in payload:
            await checks.check(payload["source_key"])
            return
        keys = (
            (await ctx.session.execute(select(Source.key).where(Source.enabled.is_(True))))
            .scalars()
            .all()
        )
        for key in keys:
            await enqueue(
                ctx.session,
                "sources.check",
                {"source_key": key},
                unique_key=f"sources.check:{key}:{datetime.now(UTC):%G-%V}",
            )

    @registry.job("sources.reconcile")
    async def reconcile(ctx: JobContext, payload: dict[str, Any]) -> None:
        if datetime.now(UTC).weekday() != 0 and not payload.get("force"):
            return  # scheduled daily, runs on Mondays
        rows = (
            await ctx.session.execute(
                select(Source.key, Source.strategies).where(Source.enabled.is_(True))
            )
        ).all()
        for key, strategies in rows:
            if len(strategies) < 2:
                continue
            for stream in get_connector(key).streams:
                try:
                    report = await runner.reconcile(key, stream)
                except Exception:
                    log.exception("reconcile failed for %s/%s", key, stream)
                    continue
                if not report.consistent:
                    log.warning(
                        "sources reconcile %s/%s: %d only in %s, %d only in %s, %d different",
                        key,
                        stream,
                        len(report.only_in_first),
                        report.strategies[0],
                        len(report.only_in_second),
                        report.strategies[1],
                        len(report.different),
                    )

    @registry.job("sources.dispatch")
    async def dispatch(ctx: JobContext, payload: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        rows = (
            await ctx.session.execute(
                select(Source.key, Source.schedule, func.max(Run.started_at))
                .outerjoin(Run, Run.source_id == Source.id)
                .where(Source.enabled.is_(True), Source.schedule.is_not(None))
                .group_by(Source.key, Source.schedule)
            )
        ).all()
        for key, schedule, last in rows:
            assert schedule is not None  # filtered in the query
            due = next_run(schedule, last) if last else now
            if due <= now:
                # One job per source per slot, even if two dispatchers overlap. A source that has
                # never run has no slot yet, so it gets a fixed key for its first run.
                slot = due.isoformat() if last else "first"
                await enqueue(
                    ctx.session,
                    "sources.sync",
                    {"source_key": key},
                    unique_key=f"sources.sync:{key}:{slot}",
                )

    registry.schedule("sources-dispatch", "every 5m", "sources.dispatch")
    registry.schedule("sources-check", "daily 03:00", "sources.check")
    registry.schedule("sources-reconcile", "daily 04:00", "sources.reconcile")
