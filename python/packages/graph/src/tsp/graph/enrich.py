from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .service import GraphService
from .types import Node, as_node


@dataclass
class EnrichContext:
    org_id: str
    session: AsyncSession
    graph: GraphService


class Enricher(Protocol):
    """Adds knowledge about a node on demand (look something up, extract claims, call an API).

    Bump ``version`` when the logic changes so existing nodes are enriched again.
    """

    name: str
    version: str

    async def run(self, ctx: EnrichContext, target: Node) -> None: ...


async def enrich_on_demand(
    session: AsyncSession,
    org_id: str,
    target: Node,
    enricher: Enricher,
    graph: GraphService | None = None,
) -> bool:
    """Run ``enricher`` on ``target`` once per (target, enricher name, version).

    Returns True if it ran now, False if an earlier run already covers it (or is running). The claim on
    the work is a unique row, so two workers racing never both run it. If the enricher raises, the row
    is marked ``failed`` and the next call runs it again.
    """
    kind, id_ = as_node(target)
    claimed = (
        await session.execute(
            text(
                """
                INSERT INTO g_enrichments (org_id, target_kind, target_id, enricher, version, status)
                VALUES (:org, :kind, :id, :name, :version, 'running')
                ON CONFLICT ON CONSTRAINT uq_g_enrichments_target DO UPDATE
                  SET status = 'running', ran_at = now() WHERE g_enrichments.status = 'failed'
                RETURNING id
                """
            ),
            {
                "org": org_id,
                "kind": kind,
                "id": id_,
                "name": enricher.name,
                "version": enricher.version,
            },
        )
    ).first()
    if claimed is None:
        return False
    mark = text("UPDATE g_enrichments SET status = :status, ran_at = now() WHERE id = :id")
    try:
        await enricher.run(
            EnrichContext(org_id, session, graph or GraphService(session)), (kind, id_)
        )
    except BaseException:
        await session.execute(mark, {"status": "failed", "id": claimed.id})
        raise
    await session.execute(mark, {"status": "done", "id": claimed.id})
    return True
