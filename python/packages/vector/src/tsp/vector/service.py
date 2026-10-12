from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tsp.audit import Actor, record
from tsp.db import Database
from tsp.jobs import enqueue
from tsp.metering import record_usage
from tsp.storage.models import StoredFile

from .chunker import chunk
from .config import VectorConfig
from .extract import UnsupportedDocument, extract
from .models import Collection, Document
from .search import Reranker, hybrid_search
from .store import VectorItem

log = logging.getLogger("tsp.vector")
INGEST_JOB = "vector.ingest"
REEMBED_JOB = "vector.reembed"
_NS = uuid.UUID("5d0f2c1e-8a3b-4b9e-9d57-0c7a1f2b3c4d")


class VectorError(Exception):
    status = 400
    type = "vector_error"


class NotFound(VectorError):
    status, type = 404, "not_found"


class LimitExceeded(VectorError):
    status, type = 402, "plan_limit_exceeded"


class BadRequest(VectorError):
    status, type = 422, "bad_request"


@dataclass
class Embedded:
    vectors: list[list[float]]
    tokens: int = 0


Embedder = Callable[
    [list[str], str, str], Awaitable[Embedded]
]  # (texts, model, "document"|"query")
Reader = Callable[[str], Awaitable[bytes]]  # object key -> bytes
Limits = Callable[[AsyncSession, str], Awaitable[dict[str, int | None]]]


def ai_embedder(client: Any, *, batch_size: int = 96) -> Embedder:
    """Adapt a ``tsp.ai`` client (``client.embeddings.create``) to the embedder this module takes."""

    async def embed(texts: list[str], model: str, input_type: str) -> Embedded:
        r = await client.embeddings.create(
            model, texts, input_type=input_type, batch_size=batch_size
        )
        data = sorted(r.data, key=lambda d: d.index)
        return Embedded([d.embedding for d in data], int(r.usage.total_tokens))

    return embed


def chunk_id(document_id: str, ordinal: int, model: str) -> str:
    return uuid.uuid5(_NS, f"{document_id}:{ordinal}:{model}").hex


@dataclass
class VectorService:
    """Collections, documents and search for one app. Backends and the embedder are injected."""

    store: Any
    embedder: Embedder
    db: Database
    config: VectorConfig = field(default_factory=VectorConfig)
    reader: Reader | None = None
    limits: Limits | None = None
    reranker: Reranker | None = None

    # -- collections -------------------------------------------------------------------------

    async def create_collection(
        self, session: AsyncSession, org_id: str, name: str, embed_model: str | None = None
    ) -> Collection:
        name = name.strip()
        if not name or len(name) > 80:
            raise BadRequest("collection names are 1 to 80 characters")
        if (await self._find(session, org_id, name)) is not None:
            raise BadRequest(f"collection {name!r} already exists")
        col = Collection(
            org_id=org_id,
            name=name,
            embed_model=embed_model or self.config.embed_model,
            dims=self.config.dims,
        )
        session.add(col)
        await session.flush()
        return col

    async def _find(self, session: AsyncSession, org_id: str, name: str) -> Collection | None:
        return (
            await session.execute(
                select(Collection).where(Collection.org_id == org_id, Collection.name == name)
            )
        ).scalar_one_or_none()

    async def collection(self, session: AsyncSession, org_id: str, name: str) -> Collection:
        col = await self._find(session, org_id, name)
        if col is None:
            raise NotFound(f"no collection named {name!r}")
        return col

    async def collections(self, session: AsyncSession, org_id: str) -> list[Collection]:
        rows = await session.execute(
            select(Collection).where(Collection.org_id == org_id).order_by(Collection.name)
        )
        return list(rows.scalars())

    # -- documents ---------------------------------------------------------------------------

    async def add_document(
        self,
        session: AsyncSession,
        org_id: str,
        collection: str,
        *,
        text_: str | None = None,
        file_id: str | None = None,
        title: str = "",
        user_sub: str | None = None,
        metadata: dict[str, Any] | None = None,
        source_uri: str | None = None,
    ) -> Document:
        """Register a document and queue its indexing. The same content added twice is one document."""
        if (text_ is None) == (file_id is None):
            raise BadRequest("give either text or file_id")
        col = await self.collection(session, org_id, collection)
        meta = dict(metadata or {})
        source = source_uri  # e.g. the page URL for crawled text; uploads set "upload:<key>" below
        if text_ is not None:
            data, body = text_.encode(), text_
        else:
            f = (
                await session.execute(
                    select(StoredFile).where(
                        StoredFile.id == file_id,
                        StoredFile.org_id == org_id,
                        StoredFile.status == "ready",
                    )
                )
            ).scalar_one_or_none()
            if f is None:
                raise NotFound("no such uploaded file")
            if self.reader is None:
                raise BadRequest("file indexing is not configured")
            if f.size > self.config.max_document_bytes:
                raise BadRequest("that file is too large to index")
            data, body, source = await self.reader(f.key), None, f"upload:{f.key}"
            meta.setdefault("filename", f.filename)
            meta.setdefault("content_type", f.content_type)
            title = title or f.filename
        if len(data) > self.config.max_document_bytes:
            raise BadRequest("that document is too large to index")
        checksum = hashlib.sha256(data).hexdigest()
        existing = (
            await session.execute(
                select(Document).where(
                    Document.collection_id == col.id, Document.checksum == checksum
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.status == "failed":
                existing.status, existing.error = "pending", None
                await enqueue(
                    session,
                    INGEST_JOB,
                    {"document_id": existing.id},
                    unique_key=f"ingest:{existing.id}:retry",
                )
            return existing
        await self._check_limits(session, org_id, new_docs=1)
        doc = Document(
            org_id=org_id,
            collection_id=col.id,
            created_by=user_sub,
            source_uri=source,
            title=title[:300],
            checksum=checksum,
            body=body,
            meta=meta,
        )
        session.add(doc)
        await session.flush()
        await enqueue(session, INGEST_JOB, {"document_id": doc.id}, unique_key=f"ingest:{doc.id}")
        return doc

    async def _check_limits(self, session: AsyncSession, org_id: str, *, new_docs: int = 0) -> None:
        if self.limits is None:
            return
        caps = await self.limits(session, org_id)
        cap = caps.get("vector_docs")
        if cap is not None and new_docs:
            n = (
                await session.execute(
                    select(func.count()).select_from(Document).where(Document.org_id == org_id)
                )
            ).scalar_one()
            if n + new_docs > cap:
                raise LimitExceeded(f"your plan allows {cap} documents")

    async def _chunk_total(self, session: AsyncSession, org_id: str) -> int:
        await session.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": org_id})
        return int(
            (
                await session.execute(
                    text("SELECT count(*) FROM vector_chunks WHERE org_id = :o"), {"o": org_id}
                )
            ).scalar_one()
        )

    async def documents(
        self, session: AsyncSession, org_id: str, collection: str | None = None
    ) -> list[Document]:
        q = select(Document).where(Document.org_id == org_id).order_by(Document.created_at.desc())
        if collection:
            q = q.where(
                Document.collection_id == (await self.collection(session, org_id, collection)).id
            )
        return list((await session.execute(q.limit(500))).scalars())

    async def delete_document(
        self,
        session: AsyncSession,
        org_id: str,
        document_id: str,
        *,
        actor: Actor,
        request: Any = None,
    ) -> None:
        doc = await session.get(Document, document_id)
        if doc is None or doc.org_id != org_id:
            raise NotFound("no such document")
        doc.status = "deleting"
        await self.store.delete_document(org_id, doc.id)
        await session.delete(doc)
        await record(
            session,
            "vector.document_deleted",
            actor=actor,
            target=("vector_document", document_id),
            request=request,
        )

    async def delete_org(self, session: AsyncSession, org_id: str, *, actor: Actor) -> None:
        """Remove every vector and document an organization owns, in every backend."""
        await self.store.delete_org(org_id)
        for col in await self.collections(session, org_id):
            await session.delete(col)  # documents go with it (cascade)
        await record(session, "vector.org_deleted", actor=actor, target=("org", org_id))

    # -- indexing (runs as a job) --------------------------------------------------------------

    async def ingest(self, document_id: str) -> None:
        """Extract, chunk, embed and store one document. Safe to run twice; permanent problems
        (unreadable file, over plan limit) mark the document failed instead of retrying forever."""
        async with self.db.session() as s:
            doc = await s.get(Document, document_id)
            if doc is None or doc.status in ("ready", "deleting"):
                return
            col = await s.get(Collection, doc.collection_id)
            assert col is not None
            org_id, model, body, source = doc.org_id, col.embed_model, doc.body, doc.source_uri
            meta, title, col_id = dict(doc.meta), doc.title, col.id
        try:
            if body is None:
                if not source or not source.startswith("upload:") or self.reader is None:
                    raise UnsupportedDocument("this document has no readable source")
                body = extract(
                    await self.reader(source.removeprefix("upload:")),
                    meta.get("content_type", ""),
                    meta.get("filename", ""),
                )
            pieces = chunk(
                body, target_tokens=self.config.chunk_tokens, overlap=self.config.chunk_overlap
            )
            if not pieces:
                raise UnsupportedDocument("no text found in this document")
            async with self.db.session() as s:
                await self._check_chunk_limit(s, org_id, len(pieces))
            vectors, tokens = await self._embed([p.content for p in pieces], model, "document")
            items: list[VectorItem] = [
                {
                    "id": chunk_id(document_id, p.ordinal, model),
                    "document_id": document_id,
                    "ordinal": p.ordinal,
                    "content": p.content,
                    "embedding": v,
                    "token_count": p.tokens,
                    "embed_model": model,
                    "metadata": {**meta, "title": title},
                }
                for p, v in zip(pieces, vectors, strict=True)
            ]
            await self.store.replace_document(org_id, col_id, document_id, items)
        except (UnsupportedDocument, LimitExceeded, BadRequest) as e:
            await self._mark(document_id, "failed", str(e))
            return
        except Exception as e:
            await self._mark(document_id, "failed", f"{type(e).__name__}: {e}"[:500])
            raise
        async with self.db.session() as s:
            doc = await s.get(Document, document_id)
            if doc is None or doc.status == "deleting":  # deleted while we were working
                await self.store.delete_document(org_id, document_id)
                return
            doc.status, doc.error, doc.body = "ready", None, None
            await record_usage(
                s,
                org_id=org_id,
                kind="ai.embed",
                model=model,
                units_in=tokens,
                document_id=document_id,
            )

    async def _check_chunk_limit(self, session: AsyncSession, org_id: str, new: int) -> None:
        if self.limits is None:
            return
        cap = (await self.limits(session, org_id)).get("vector_chunks")
        if cap is not None and await self._chunk_total(session, org_id) + new > cap:
            raise LimitExceeded(f"your plan allows {cap} indexed passages")

    async def _mark(self, document_id: str, status: str, error: str | None) -> None:
        async with self.db.session() as s:
            doc = await s.get(Document, document_id)
            if doc is not None and doc.status != "deleting":
                doc.status, doc.error = status, error

    async def _embed(
        self, texts: list[str], model: str, kind: str
    ) -> tuple[list[list[float]], int]:
        vectors: list[list[float]] = []
        tokens = 0
        for i in range(0, len(texts), self.config.embed_batch):
            out = await self.embedder(texts[i : i + self.config.embed_batch], model, kind)
            vectors += out.vectors
            tokens += out.tokens
        if len(vectors) != len(texts):
            raise RuntimeError(f"embedder returned {len(vectors)} vectors for {len(texts)} texts")
        bad = next((len(v) for v in vectors if len(v) != self.config.dims), None)
        if bad is not None:
            raise RuntimeError(
                f"{model} returned {bad}-dimension vectors but the database holds {self.config.dims}"
            )
        return vectors, tokens

    # -- search --------------------------------------------------------------------------------

    async def search(
        self,
        session: AsyncSession,
        org_id: str,
        collection: str,
        query: str,
        *,
        k: int = 10,
        filter: dict[str, Any] | None = None,
        api_key_id: str | None = None,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            raise BadRequest("the query is empty")
        k = max(1, min(k, 50))
        col = await self.collection(session, org_id, collection)
        vectors, tokens = await self._embed([query], col.embed_model, "query")
        hits = await hybrid_search(
            self.store,
            org_id,
            col.id,
            query,
            vectors[0],
            k=k,
            filter=filter,
            model=col.embed_model,
            candidates=self.config.candidates,
            rerank=self.reranker,
        )
        await record_usage(
            session,
            org_id=org_id,
            kind="ai.embed",
            model=col.embed_model,
            units_in=tokens,
            api_key_id=api_key_id,
            request_id=request_id,
            purpose="query",
        )
        await record_usage(
            session,
            org_id=org_id,
            kind="vector.search",
            units_out=len(hits),
            api_key_id=api_key_id,
            request_id=request_id,
        )
        docs = {
            d.id: d
            for d in (
                await session.execute(
                    select(Document).where(
                        Document.org_id == org_id, Document.id.in_({h["document_id"] for h in hits})
                    )
                )
            ).scalars()
        }
        return [
            {
                "chunk_id": h["id"],
                "document_id": h["document_id"],
                "title": docs[h["document_id"]].title if h["document_id"] in docs else "",
                "source": docs[h["document_id"]].source_uri if h["document_id"] in docs else None,
                "score": h["score"],
                "content": h["content"],
            }
            for h in hits
            if h["document_id"] in docs
        ]

    # -- changing the embedding model ------------------------------------------------------------

    async def request_reembed(
        self, session: AsyncSession, org_id: str, collection: str, model: str, *, actor: Actor
    ) -> None:
        col = await self.collection(session, org_id, collection)
        if model == col.embed_model:
            raise BadRequest("the collection already uses that model")
        await enqueue(
            session,
            REEMBED_JOB,
            {"collection_id": col.id, "model": model},
            unique_key=f"reembed:{col.id}:{model}",
        )
        await record(
            session,
            "vector.reembed_requested",
            actor=actor,
            target=("vector_collection", col.id),
            model=model,
        )

    async def reembed(self, collection_id: str, model: str) -> None:
        """New vectors are written beside the old ones (queries keep using the old model); when every
        document is done the collection switches and the old vectors go."""
        async with self.db.session() as s:
            col = await s.get(Collection, collection_id)
            if col is None or col.embed_model == model:
                return
            org_id = col.org_id
            ids = list(
                (
                    await s.execute(
                        select(Document.id).where(
                            Document.collection_id == collection_id, Document.status == "ready"
                        )
                    )
                ).scalars()
            )
            old = col.embed_model
        total = 0
        for doc_id in ids:
            rows = await self.store.chunks(org_id, collection_id, doc_id, old)
            if not rows:
                continue
            vectors, tokens = await self._embed([r["content"] for r in rows], model, "document")
            total += tokens
            await self.store.upsert(
                org_id,
                collection_id,
                [
                    {
                        "id": chunk_id(doc_id, r["ordinal"], model),
                        "document_id": doc_id,
                        "ordinal": r["ordinal"],
                        "content": r["content"],
                        "embedding": v,
                        "token_count": r["token_count"],
                        "embed_model": model,
                        "metadata": r["metadata"],
                    }
                    for r, v in zip(rows, vectors, strict=True)
                ],
            )
        async with self.db.session() as s:
            col = await s.get(Collection, collection_id)
            assert col is not None
            col.embed_model = model
            await record_usage(
                s, org_id=org_id, kind="ai.embed", model=model, units_in=total, purpose="reembed"
            )
        await self.store.delete_other_models(org_id, collection_id, model)
