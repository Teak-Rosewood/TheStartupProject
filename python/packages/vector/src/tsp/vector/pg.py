from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.db import Database

from .lang import detect_lang
from .store import Hit, VectorItem


def vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


async def scope(session: AsyncSession, org_id: str) -> None:
    """Bind this transaction to one organization for row-level security."""
    await session.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": org_id})


_INSERT = text(
    """
    INSERT INTO vector_chunks (id, org_id, collection_id, document_id, ordinal, content,
                               token_count, embedding, embed_model, metadata, lang)
    VALUES (:id, :org, :col, :doc, :ord, :content, :tok, CAST(:emb AS halfvec), :model,
            CAST(:meta AS jsonb), :lang)
    ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, token_count = EXCLUDED.token_count,
        embedding = EXCLUDED.embedding, embed_model = EXCLUDED.embed_model,
        metadata = EXCLUDED.metadata, lang = EXCLUDED.lang
    WHERE vector_chunks.org_id = EXCLUDED.org_id
    """
)


def _rows(org_id: str, collection_id: str, items: list[VectorItem]) -> list[dict[str, Any]]:
    return [
        {
            "id": i["id"],
            "org": org_id,
            "col": collection_id,
            "doc": i["document_id"],
            "ord": i["ordinal"],
            "content": i["content"],
            "tok": i["token_count"],
            "emb": vec(i["embedding"]),
            "model": i["embed_model"],
            "meta": json.dumps(i["metadata"]),
            "lang": i.get("lang") or detect_lang(i["content"]),
        }
        for i in items
    ]


class PgVectorStore:
    """pgvector in the application database. Opens one short transaction per call, scoped to the org."""

    def __init__(self, db: Database | None = None):
        self._db = db

    def bind(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        if self._db is None:
            raise RuntimeError("PgVectorStore has no database; pass db= or call bind(db)")
        return self._db

    async def upsert(self, org_id: str, collection_id: str, items: list[VectorItem]) -> None:
        if not items:
            return
        async with self.db.session() as s:
            await scope(s, org_id)
            await s.execute(_INSERT, _rows(org_id, collection_id, items))

    async def replace_document(
        self, org_id: str, collection_id: str, document_id: str, items: list[VectorItem]
    ) -> None:
        async with self.db.session() as s:
            await scope(s, org_id)
            await s.execute(
                text("DELETE FROM vector_chunks WHERE org_id = :o AND document_id = :d"),
                {"o": org_id, "d": document_id},
            )
            if items:
                await s.execute(_INSERT, _rows(org_id, collection_id, items))

    @staticmethod
    def _where(filter: dict[str, Any] | None, model: str | None) -> tuple[str, dict[str, Any]]:
        sql, params = "", {}
        if filter:
            sql += " AND metadata @> CAST(:flt AS jsonb)"
            params["flt"] = json.dumps(filter)
        if model:
            sql += " AND embed_model = :model"
            params["model"] = model
        return sql, params

    async def query(
        self,
        org_id: str,
        collection_id: str,
        vector: list[float],
        k: int,
        filter: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> list[Hit]:
        extra, params = self._where(filter, model)
        async with self.db.session() as s:
            await scope(s, org_id)
            await s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            rows = await s.execute(
                text(
                    f"""
                    SELECT id, document_id, content, metadata,
                           1 - (embedding <=> CAST(:v AS halfvec)) AS score
                    FROM vector_chunks
                    WHERE org_id = :o AND collection_id = :c{extra}
                    ORDER BY embedding <=> CAST(:v AS halfvec) LIMIT :k
                    """
                ),
                {"v": vec(vector), "o": org_id, "c": collection_id, "k": k, **params},
            )
            return [_hit(r) for r in rows]

    async def text_query(
        self,
        org_id: str,
        collection_id: str,
        text_: str,
        k: int,
        filter: dict[str, Any] | None = None,
        model: str | None = None,
        lang: str | None = None,
    ) -> list[Hit]:
        extra, params = self._where(filter, model)
        # Same configuration the index used for that language (see tsp_tsv in migration 0002).
        query = (
            "plainto_tsquery('simple', tsp_norm_devanagari(:t))"
            if (lang or detect_lang(text_)) == "hi"
            else "plainto_tsquery('english', :t)"
        )
        async with self.db.session() as s:
            await scope(s, org_id)
            rows = await s.execute(
                text(
                    f"""
                    SELECT id, document_id, content, metadata,
                           ts_rank_cd(tsv, q) AS score
                    FROM vector_chunks, {query} q
                    WHERE org_id = :o AND collection_id = :c AND tsv @@ q{extra}
                    ORDER BY score DESC LIMIT :k
                    """
                ),
                {"t": text_, "o": org_id, "c": collection_id, "k": k, **params},
            )
            return [_hit(r) for r in rows]

    async def delete(self, org_id: str, collection_id: str, ids: list[str]) -> None:
        if not ids:
            return
        async with self.db.session() as s:
            await scope(s, org_id)
            await s.execute(
                text(
                    "DELETE FROM vector_chunks WHERE org_id = :o AND collection_id = :c AND id = ANY(:ids)"
                ),
                {"o": org_id, "c": collection_id, "ids": ids},
            )

    async def delete_document(self, org_id: str, document_id: str) -> None:
        async with self.db.session() as s:
            await scope(s, org_id)
            await s.execute(
                text("DELETE FROM vector_chunks WHERE org_id = :o AND document_id = :d"),
                {"o": org_id, "d": document_id},
            )

    async def chunks(
        self, org_id: str, collection_id: str, document_id: str, model: str | None = None
    ) -> list[dict[str, Any]]:
        extra, params = ("", {}) if not model else (" AND embed_model = :m", {"m": model})
        async with self.db.session() as s:
            await scope(s, org_id)
            rows = await s.execute(
                text(
                    "SELECT ordinal, content, token_count, metadata FROM vector_chunks "
                    f"WHERE org_id = :o AND document_id = :d{extra} ORDER BY ordinal"
                ),
                {"o": org_id, "d": document_id, **params},
            )
            return [
                {
                    "ordinal": r.ordinal,
                    "content": r.content,
                    "token_count": r.token_count,
                    "metadata": r.metadata,
                }
                for r in rows
            ]

    async def delete_other_models(self, org_id: str, collection_id: str, keep_model: str) -> None:
        async with self.db.session() as s:
            await scope(s, org_id)
            await s.execute(
                text(
                    "DELETE FROM vector_chunks WHERE org_id = :o AND collection_id = :c AND embed_model <> :m"
                ),
                {"o": org_id, "c": collection_id, "m": keep_model},
            )

    async def delete_org(self, org_id: str) -> None:
        async with self.db.session() as s:
            await scope(s, org_id)
            await s.execute(text("DELETE FROM vector_chunks WHERE org_id = :o"), {"o": org_id})


def _hit(r: Any) -> Hit:
    return {
        "id": r.id,
        "document_id": r.document_id,
        "score": float(r.score),
        "content": r.content,
        "metadata": r.metadata,
    }
