from __future__ import annotations

from typing import Any

from tsp.jobs import JobContext, JobRegistry

from .service import CmsService

PUBLISH_DUE = "cms.publish_due"
LIFECYCLE = "cms.lifecycle"


def register_jobs(registry: JobRegistry, **service_kwargs: Any) -> None:
    """``cms.publish_due`` every minute: publish scheduled items that are due. ``cms.lifecycle`` daily:
    review reminders and automatic freezing."""

    @registry.job(PUBLISH_DUE)
    async def publish_due(ctx: JobContext, payload: dict[str, Any]) -> None:
        await CmsService(ctx.session, **service_kwargs).publish_due()

    registry.schedule("cms-publish-due", "every 1m", PUBLISH_DUE)

    @registry.job(LIFECYCLE)
    async def lifecycle(ctx: JobContext, payload: dict[str, Any]) -> None:
        await CmsService(ctx.session, **service_kwargs).run_lifecycle()

    registry.schedule("cms-lifecycle", "daily 05:20", LIFECYCLE)
