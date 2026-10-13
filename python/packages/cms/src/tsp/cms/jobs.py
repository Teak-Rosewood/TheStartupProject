from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tsp.jobs import JobContext, JobRegistry

from .complaints import ComplaintsDesk
from .service import CmsService
from .windows import sync_window_flags

PUBLISH_DUE = "cms.publish_due"
LIFECYCLE = "cms.lifecycle"
COMPLAINTS_SLA = "cms.complaints_sla"
WINDOWS_SYNC = "cms.windows_sync"


def register_jobs(
    registry: JobRegistry,
    *,
    flags: Any = None,
    desk: Callable[[AsyncSession], ComplaintsDesk] | None = None,
    **service_kwargs: Any,
) -> None:
    """``cms.publish_due`` every minute: publish scheduled items that are due. ``cms.lifecycle`` daily:
    review reminders and automatic freezing. With ``desk``, ``cms.complaints_sla`` hourly: warn before
    and escalate after complaint deadlines. With ``flags``, ``cms.windows_sync`` every minute: keep the
    ``cms.window.<name>`` flags in step with the restricted-mode windows."""

    @registry.job(PUBLISH_DUE)
    async def publish_due(ctx: JobContext, payload: dict[str, Any]) -> None:
        await CmsService(ctx.session, **service_kwargs).publish_due()

    registry.schedule("cms-publish-due", "every 1m", PUBLISH_DUE)

    @registry.job(LIFECYCLE)
    async def lifecycle(ctx: JobContext, payload: dict[str, Any]) -> None:
        await CmsService(ctx.session, **service_kwargs).run_lifecycle()

    registry.schedule("cms-lifecycle", "daily 05:20", LIFECYCLE)

    if desk is not None:

        @registry.job(COMPLAINTS_SLA)
        async def complaints_sla(ctx: JobContext, payload: dict[str, Any]) -> None:
            await desk(ctx.session).run_sla()

        registry.schedule("cms-complaints-sla", "every 1h", COMPLAINTS_SLA)

    if flags is not None:
        clock = service_kwargs.get("clock")

        @registry.job(WINDOWS_SYNC)
        async def windows_sync(ctx: JobContext, payload: dict[str, Any]) -> None:
            await sync_window_flags(ctx.session, flags, **({"clock": clock} if clock else {}))

        registry.schedule("cms-windows-sync", "every 1m", WINDOWS_SYNC)
