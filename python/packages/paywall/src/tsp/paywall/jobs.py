from __future__ import annotations

from typing import Any

from .service import Paywall


def register_jobs(registry: Any, pw: Paywall) -> None:
    """A daily job that applies the retention schedule to reading history."""

    @registry.job("paywall.purge")
    async def purge(ctx: Any, payload: dict[str, Any]) -> None:
        await pw.purge(ctx.session)

    registry.schedule("paywall-purge", "daily 03:40", "paywall.purge")
