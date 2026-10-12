from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import CrawlPage, CrawlSource
from .service import CrawlService


def register_with_compliance(compliance: Any, service: CrawlService) -> None:
    """Export the sources a user added with their page URLs; delete them (and their indexed
    documents, through the purge job) when the user leaves."""

    async def export(session: AsyncSession, sub: str) -> list[dict[str, Any]]:
        sources = (
            await session.execute(select(CrawlSource).where(CrawlSource.created_by == sub))
        ).scalars()
        out = []
        for src in sources:
            urls = (
                await session.execute(
                    select(CrawlPage.url)
                    .where(CrawlPage.source_id == src.id)
                    .order_by(CrawlPage.url)
                )
            ).scalars()
            out.append({"seed_url": src.seed_url, "status": src.status, "pages": list(urls)})
        return out

    async def erase(session: AsyncSession, sub: str) -> None:
        from tsp.jobs import enqueue

        from .service import PURGE_JOB

        sources = (
            await session.execute(select(CrawlSource).where(CrawlSource.created_by == sub))
        ).scalars()
        for src in sources:
            src.status = "deleting"
            await enqueue(
                session, PURGE_JOB, {"source_id": src.id}, unique_key=f"crawl.purge:{src.id}"
            )

    compliance.register_exporter("crawl", export)
    compliance.register_eraser("crawl", erase)
