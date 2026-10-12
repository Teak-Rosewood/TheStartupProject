from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any, Literal

from sqlalchemy import and_, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Alias, Claim, Document, Entity, Link, Stance
from .resolve import normalise
from .types import Candidate, Edge, Node, Path, as_node

AliasInput = str | tuple[str, str | None]  # "text" or ("text", "lang")
_VALID = (
    "(CAST(:at AS date) IS NULL OR ((l.valid_from IS NULL OR l.valid_from <= CAST(:at AS date)) "
    "AND (l.valid_to IS NULL OR CAST(:at AS date) < l.valid_to)))"
)
_RELS = "(CAST(:rels AS text[]) IS NULL OR l.rel = ANY(CAST(:rels AS text[])))"
_EDGE_COLS = (
    "l.id, l.src_kind, l.src_id, l.rel, l.dst_kind, l.dst_id, l.valid_from, l.valid_to, "
    "l.attrs, l.evidence_claim_id"
)


def _edge(row: Any, depth: int = 1) -> Edge:
    return Edge(
        id=row.id,
        src=(row.src_kind, row.src_id),
        rel=row.rel,
        dst=(row.dst_kind, row.dst_id),
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        depth=depth,
        attrs=row.attrs,
        evidence_claim_id=row.evidence_claim_id,
    )


def _key(n: Node) -> str:
    return f"{n[0]}:{n[1]}"


def _parse_key(k: str) -> Node:
    kind, _, id_ = k.partition(":")
    return kind, uuid.UUID(id_)


class GraphService:
    """Entities, claims, links, stances and queries for one session. Every call names its ``org_id``.

    ``exclusive`` opts relations into "latest wins": ``{"holds_role": "dst"}`` means a role has one
    holder at a time, so linking a new holder from a date closes the previous holder's link on that
    date; ``{"member_of": "src"}`` means an entity belongs to one group at a time.
    """

    def __init__(
        self, session: AsyncSession, exclusive: Mapping[str, Literal["src", "dst"]] | None = None
    ):
        self.s = session
        self.exclusive = dict(exclusive or {})

    # -- entities ------------------------------------------------------------------------------

    async def upsert_entity(
        self,
        org_id: str,
        kind: str,
        name: str,
        aliases: Iterable[AliasInput] = (),
        attrs: Mapping[str, Any] | None = None,
        external_ids: Mapping[str, Any] | None = None,
        lang: str | None = None,
    ) -> Entity:
        """Find the entity by external id, else by an alias equal to ``name``, else create it.

        Attributes and external ids are merged into an existing entity; aliases are added.
        """
        entity = await self._find_entity(org_id, kind, name, external_ids or {})
        if entity is None:
            entity = Entity(
                org_id=org_id,
                kind=kind,
                name=name,
                attrs=dict(attrs or {}),
                external_ids=dict(external_ids or {}),
            )
            self.s.add(entity)
            await self.s.flush()
        else:
            entity.attrs = {**entity.attrs, **(attrs or {})}
            entity.external_ids = {**entity.external_ids, **(external_ids or {})}
        await self.add_aliases(org_id, entity.id, [(name, lang), *aliases])
        await self.s.flush()
        return entity

    async def _find_entity(
        self, org_id: str, kind: str, name: str, external_ids: Mapping[str, Any]
    ) -> Entity | None:
        for k, v in external_ids.items():
            found = (
                await self.s.execute(
                    select(Entity)
                    .where(Entity.org_id == org_id, Entity.kind == kind)
                    .where(Entity.external_ids.contains({k: v}))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if found:
                return found
        return (
            await self.s.execute(
                select(Entity)
                .join(Alias, Alias.entity_id == Entity.id)
                .where(Entity.org_id == org_id, Entity.kind == kind)
                .where(Alias.normalised == normalise(name))
                .order_by(Entity.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()

    async def add_aliases(
        self, org_id: str, entity_id: uuid.UUID, aliases: Iterable[AliasInput]
    ) -> None:
        rows: dict[str, dict[str, Any]] = {}
        for a in aliases:
            alias, lang = (a, None) if isinstance(a, str) else a
            norm = normalise(alias)
            if norm and norm not in rows:
                rows[norm] = {
                    "org_id": org_id,
                    "entity_id": entity_id,
                    "alias": alias,
                    "lang": lang,
                    "normalised": norm,
                }
        if rows:
            await self.s.execute(
                insert(Alias)
                .values(list(rows.values()))
                .on_conflict_do_nothing(constraint="uq_g_aliases_entity_normalised")
            )

    async def resolve(
        self,
        org_id: str,
        mention: str,
        kind: str | None = None,
        lang: str | None = None,
        min_score: float = 0.4,
        limit: int = 5,
    ) -> list[Candidate]:
        """Entities a mention may refer to: exact alias matches first, then trigram look-alikes.

        Matches in ``lang`` rank above equal matches in other languages. Returns candidates; deciding
        between several is the caller's job (or an LLM's).
        """
        norm = normalise(mention)
        if not norm:
            return []
        rows = (
            await self.s.execute(
                text(
                    """
                    SELECT DISTINCT ON (e.id) e.id, a.alias, a.lang, similarity(a.normalised, :n) AS sim,
                           (a.normalised = :n) AS exact
                    FROM g_aliases a JOIN g_entities e ON e.id = a.entity_id
                    WHERE a.org_id = :org AND (CAST(:kind AS text) IS NULL OR e.kind = :kind)
                      AND (a.normalised = :n OR (a.normalised % :n AND similarity(a.normalised, :n) >= :min))
                    ORDER BY e.id, (a.normalised = :n) DESC, (a.lang IS NOT DISTINCT FROM :lang) DESC, sim DESC
                    """
                ),
                {"n": norm, "org": org_id, "kind": kind, "lang": lang, "min": min_score},
            )
        ).all()
        entities = {
            e.id: e
            for e in (
                await self.s.execute(select(Entity).where(Entity.id.in_([r.id for r in rows])))
            ).scalars()
        }
        found = [
            Candidate(
                entities[r.id],
                1.0 if r.exact else float(r.sim),
                r.alias,
                "exact" if r.exact else "trigram",
            )
            for r in rows
        ]
        found.sort(key=lambda c: (-c.score, c.entity.created_at))
        return found[:limit]

    # -- documents, claims, stances ----------------------------------------------------------------

    async def add_document(self, org_id: str, **fields: Any) -> Document:
        doc = Document(org_id=org_id, **fields)
        self.s.add(doc)
        await self.s.flush()
        return doc

    async def add_claim(
        self,
        org_id: str,
        subject_id: uuid.UUID,
        predicate: str,
        text: str,
        *,
        object_id: uuid.UUID | None = None,
        object_value: Any = None,
        document_id: uuid.UUID | None = None,
        chunk_id: uuid.UUID | None = None,
        quote: tuple[int, int] | None = None,
        confidence: float | None = None,
        extracted_by: str | None = None,
        status: str = "auto",
    ) -> Claim:
        """A statement with its source: the document, the chunk and the character range quoted."""
        if object_id is not None and object_value is not None:
            raise ValueError("a claim has an object entity or an object value, not both")
        claim = Claim(
            org_id=org_id,
            text=text,
            subject_id=subject_id,
            predicate=predicate,
            object_id=object_id,
            object_value=object_value,
            document_id=document_id,
            chunk_id=chunk_id,
            quote_start=quote[0] if quote else None,
            quote_end=quote[1] if quote else None,
            confidence=confidence,
            extracted_by=extracted_by,
            status=status,
        )
        self.s.add(claim)
        await self.s.flush()
        return claim

    async def set_status(self, org_id: str, claim_id: uuid.UUID, status: str) -> None:
        await self.s.execute(
            update(Claim).where(Claim.org_id == org_id, Claim.id == claim_id).values(status=status)
        )

    async def set_stance(
        self,
        org_id: str,
        holder_id: uuid.UUID,
        topic_key: str,
        position: float | None,
        as_of: date,
        evidence_claim_id: uuid.UUID | None = None,
        label: str | None = None,
        confidence: float | None = None,
    ) -> Stance:
        """Record where ``holder_id`` stood on a topic on ``as_of``; earlier stances are kept."""
        stance = Stance(
            org_id=org_id,
            holder_id=holder_id,
            topic_key=topic_key,
            position=position,
            label=label,
            as_of=as_of,
            evidence_claim_id=evidence_claim_id,
            confidence=confidence,
        )
        self.s.add(stance)
        await self.s.flush()
        return stance

    async def stances(self, org_id: str, topic_key: str, at: date | None = None) -> list[Stance]:
        """Each holder's latest stance on the topic (as of ``at`` when given)."""
        q = select(Stance).where(Stance.org_id == org_id, Stance.topic_key == topic_key)
        if at:
            q = q.where(Stance.as_of <= at)
        q = q.distinct(Stance.holder_id).order_by(Stance.holder_id, Stance.as_of.desc())
        return list((await self.s.execute(q)).scalars())

    # -- links -----------------------------------------------------------------------------------

    async def link(
        self,
        org_id: str,
        src: Node | Entity | Document | Claim,
        rel: str,
        dst: Node | Entity | Document | Claim,
        valid_from: date | None = None,
        valid_to: date | None = None,
        evidence: uuid.UUID | None = None,
        attrs: Mapping[str, Any] | None = None,
    ) -> Link:
        """Add an edge valid from ``valid_from`` up to, not including, ``valid_to``.

        For relations listed in ``exclusive``, links that are still open on ``valid_from`` and share
        the exclusive end are closed on that date.
        """
        s_kind, s_id = as_node(src)
        d_kind, d_id = as_node(dst)
        if valid_from and valid_to and valid_to < valid_from:
            raise ValueError("valid_to is before valid_from")
        side = self.exclusive.get(rel)
        if side and valid_from:
            kind, id_ = (s_kind, s_id) if side == "src" else (d_kind, d_id)
            await self.s.execute(
                update(Link)
                .where(
                    Link.org_id == org_id,
                    Link.rel == rel,
                    getattr(Link, f"{side}_kind") == kind,
                    getattr(Link, f"{side}_id") == id_,
                    or_(Link.valid_to.is_(None), Link.valid_to > valid_from),
                    or_(Link.valid_from.is_(None), Link.valid_from < valid_from),
                )
                .values(valid_to=valid_from)
            )
        row = Link(
            org_id=org_id,
            src_kind=s_kind,
            src_id=s_id,
            rel=rel,
            dst_kind=d_kind,
            dst_id=d_id,
            valid_from=valid_from,
            valid_to=valid_to,
            evidence_claim_id=evidence,
            attrs=dict(attrs or {}),
        )
        self.s.add(row)
        await self.s.flush()
        return row

    async def end_link(self, org_id: str, link_id: uuid.UUID, on: date) -> None:
        await self.s.execute(
            update(Link).where(Link.org_id == org_id, Link.id == link_id).values(valid_to=on)
        )

    # -- queries ---------------------------------------------------------------------------------

    async def valid_at(
        self,
        org_id: str,
        node: Node | Entity | Document | Claim,
        rel: str,
        at: date,
    ) -> list[Edge]:
        """Edges of ``rel`` touching ``node`` that were valid on ``at``."""
        kind, id_ = as_node(node)
        rows = await self.s.execute(
            text(
                f"""
                SELECT {_EDGE_COLS} FROM g_links l
                WHERE l.org_id = :org AND l.rel = :rel AND {_VALID}
                  AND ((l.src_kind = :kind AND l.src_id = :id) OR (l.dst_kind = :kind AND l.dst_id = :id))
                ORDER BY l.valid_from NULLS FIRST, l.created_at
                """
            ),
            {"org": org_id, "rel": rel, "at": at, "kind": kind, "id": id_},
        )
        return [_edge(r, 0) for r in rows]

    async def holders(
        self,
        org_id: str,
        role: Node | Entity,
        at: date,
        rel: str = "holds_role",
    ) -> list[Entity]:
        """Entities linked to ``role`` by ``rel`` on ``at``: who held the position on that date."""
        kind, id_ = as_node(role)
        q = (
            select(Entity)
            .join(Link, and_(Link.src_kind == "entity", Link.src_id == Entity.id))
            .where(
                Link.org_id == org_id,
                Link.rel == rel,
                Link.dst_kind == kind,
                Link.dst_id == id_,
                or_(Link.valid_from.is_(None), Link.valid_from <= at),
                or_(Link.valid_to.is_(None), Link.valid_to > at),
            )
            .order_by(Link.valid_from.nulls_first(), Link.created_at)
        )
        return list((await self.s.execute(q)).scalars())

    async def neighbours(
        self,
        org_id: str,
        node: Node | Entity | Document | Claim,
        rels: Sequence[str] | None = None,
        depth: int = 1,
        at: date | None = None,
    ) -> list[Edge]:
        """Edges within ``depth`` hops of ``node`` in either direction, nearest first.

        Each node is visited once per route, so cycles end. ``at`` keeps only edges valid on that date.
        """
        kind, id_ = as_node(node)
        rows = await self.s.execute(
            text(
                f"""
                WITH RECURSIVE walk(kind, id, depth, visited, link_id) AS (
                  SELECT CAST(:kind AS text), CAST(:id AS uuid), 0,
                         ARRAY[CAST(:kind AS text) || ':' || CAST(:id AS text)], CAST(NULL AS uuid)
                  UNION ALL
                  SELECT o.kind, o.id, w.depth + 1, w.visited || (o.kind || ':' || o.id::text), l.id
                  FROM walk w
                  JOIN g_links l ON l.org_id = :org
                   AND ((l.src_kind = w.kind AND l.src_id = w.id) OR (l.dst_kind = w.kind AND l.dst_id = w.id))
                  CROSS JOIN LATERAL (
                    SELECT CASE WHEN l.src_kind = w.kind AND l.src_id = w.id THEN l.dst_kind ELSE l.src_kind END AS kind,
                           CASE WHEN l.src_kind = w.kind AND l.src_id = w.id THEN l.dst_id ELSE l.src_id END AS id
                  ) o
                  WHERE w.depth < :depth AND {_RELS} AND {_VALID}
                    AND NOT (o.kind || ':' || o.id::text) = ANY(w.visited)
                )
                SELECT {_EDGE_COLS}, d.depth
                FROM (SELECT link_id, min(depth) AS depth FROM walk WHERE link_id IS NOT NULL GROUP BY link_id) d
                JOIN g_links l ON l.id = d.link_id
                ORDER BY d.depth, l.created_at, l.id
                """
            ),
            {
                "org": org_id,
                "kind": kind,
                "id": str(id_),
                "depth": max(0, depth),
                "rels": list(rels) if rels else None,
                "at": at,
            },
        )
        return [_edge(r, r.depth) for r in rows]

    async def paths(
        self,
        org_id: str,
        a: Node | Entity | Document | Claim,
        b: Node | Entity | Document | Claim,
        max_hops: int = 3,
        rels: Sequence[str] | None = None,
        at: date | None = None,
        limit: int = 10,
    ) -> list[Path]:
        """Shortest routes from ``a`` to ``b`` (up to ``max_hops`` edges), never revisiting a node."""
        (ak, ai), (bk, bi) = as_node(a), as_node(b)
        rows = (
            await self.s.execute(
                text(
                    f"""
                    WITH RECURSIVE walk(kind, id, depth, nodes, links) AS (
                      SELECT CAST(:ak AS text), CAST(:ai AS uuid), 0,
                             ARRAY[CAST(:ak AS text) || ':' || CAST(:ai AS text)], ARRAY[]::uuid[]
                      UNION ALL
                      SELECT o.kind, o.id, w.depth + 1, w.nodes || (o.kind || ':' || o.id::text), w.links || l.id
                      FROM walk w
                      JOIN g_links l ON l.org_id = :org
                       AND ((l.src_kind = w.kind AND l.src_id = w.id) OR (l.dst_kind = w.kind AND l.dst_id = w.id))
                      CROSS JOIN LATERAL (
                        SELECT CASE WHEN l.src_kind = w.kind AND l.src_id = w.id THEN l.dst_kind ELSE l.src_kind END AS kind,
                               CASE WHEN l.src_kind = w.kind AND l.src_id = w.id THEN l.dst_id ELSE l.src_id END AS id
                      ) o
                      WHERE w.depth < :hops AND NOT (w.kind = :bk AND w.id = CAST(:bi AS uuid))
                        AND {_RELS} AND {_VALID}
                        AND NOT (o.kind || ':' || o.id::text) = ANY(w.nodes)
                    )
                    SELECT nodes, links FROM walk
                    WHERE kind = :bk AND id = CAST(:bi AS uuid) AND depth > 0
                    ORDER BY depth LIMIT :limit
                    """
                ),
                {
                    "org": org_id,
                    "ak": ak,
                    "ai": str(ai),
                    "bk": bk,
                    "bi": str(bi),
                    "hops": max(0, max_hops),
                    "rels": list(rels) if rels else None,
                    "at": at,
                    "limit": limit,
                },
            )
        ).all()
        ids = {i for r in rows for i in r.links}
        edges = {
            e.id: e
            for e in (
                _edge(r)
                for r in await self.s.execute(
                    text(
                        f"SELECT {_EDGE_COLS} FROM g_links l WHERE l.id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": [str(i) for i in ids]},
                )
            )
        }
        return [Path([_parse_key(k) for k in r.nodes], [edges[i] for i in r.links]) for r in rows]

    # -- search and deletion -----------------------------------------------------------------------

    async def search_entities(
        self, org_id: str, q: str, kind: str | None = None, limit: int = 20
    ) -> list[Entity]:
        """Entities whose alias contains the text (case and accent insensitive)."""
        norm = normalise(q)
        stmt = (
            select(Entity)
            .join(Alias, Alias.entity_id == Entity.id)
            .where(
                Entity.org_id == org_id,
                Alias.normalised.like(f"%{_like_escape(norm)}%", escape="\\"),
            )
            .group_by(Entity.id)
            .order_by(Entity.name)
            .limit(limit)
        )
        if kind:
            stmt = stmt.where(Entity.kind == kind)
        return list((await self.s.execute(stmt)).scalars())

    async def delete_org(self, org_id: str) -> None:
        """Remove everything an organization has in the graph (organization deletion)."""
        from .models import Enrichment, Observation, Series

        for model in (
            Observation,
            Series,
            Enrichment,
            Stance,
            Link,
            Claim,
            Document,
            Alias,
            Entity,
        ):
            await self.s.execute(delete(model).where(model.org_id == org_id))


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
