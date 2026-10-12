"""The store contract every vector backend implements. ``org_id`` is a required argument everywhere."""

from __future__ import annotations

from typing import Any, Protocol, TypedDict


class VectorItem(TypedDict):
    id: str
    document_id: str
    ordinal: int
    content: str
    embedding: list[float]
    token_count: int
    embed_model: str
    metadata: dict[str, Any]


class Hit(TypedDict):
    id: str
    document_id: str
    score: float
    content: str
    metadata: dict[str, Any]


class VectorStore(Protocol):
    async def upsert(self, org_id: str, collection_id: str, items: list[VectorItem]) -> None: ...

    async def replace_document(
        self, org_id: str, collection_id: str, document_id: str, items: list[VectorItem]
    ) -> None:
        """Atomically make ``items`` the only chunks of the document (retries never duplicate)."""
        ...

    async def query(
        self,
        org_id: str,
        collection_id: str,
        vector: list[float],
        k: int,
        filter: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> list[Hit]: ...

    async def delete(self, org_id: str, collection_id: str, ids: list[str]) -> None: ...

    async def delete_document(self, org_id: str, document_id: str) -> None: ...

    async def delete_org(self, org_id: str) -> None: ...

    async def chunks(
        self, org_id: str, collection_id: str, document_id: str, model: str | None = None
    ) -> list[dict[str, Any]]:
        """A document's stored text pieces (``ordinal``, ``content``, ``token_count``, ``metadata``),
        in order. Used to re-embed without the original file."""
        ...

    async def delete_other_models(self, org_id: str, collection_id: str, keep_model: str) -> None:
        """Drop vectors made by any model but ``keep_model`` (the last step of a re-embed)."""
        ...


class TextSearch(Protocol):
    """Optional: stores with built-in keyword search take part in hybrid search."""

    async def text_query(
        self,
        org_id: str,
        collection_id: str,
        text: str,
        k: int,
        filter: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> list[Hit]: ...
