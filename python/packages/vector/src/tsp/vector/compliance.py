from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Document
from .service import VectorService


def register_with_compliance(compliance: Any, service: VectorService) -> None:
    """Export the documents a user added, and delete them (with their vectors) when they leave."""

    async def export(session: AsyncSession, sub: str) -> list[dict[str, Any]]:
        rows = (await session.execute(select(Document).where(Document.created_by == sub))).scalars()
        return [
            {"title": d.title, "source": d.source_uri, "status": d.status, "added_at": d.created_at}
            for d in rows
        ]

    async def erase(session: AsyncSession, sub: str) -> None:
        docs = (
            (await session.execute(select(Document).where(Document.created_by == sub)))
            .scalars()
            .all()
        )
        for d in docs:
            await service.store.delete_document(d.org_id, d.id)
            await session.delete(d)

    compliance.register_exporter("vector", export)
    compliance.register_eraser("vector", erase)
