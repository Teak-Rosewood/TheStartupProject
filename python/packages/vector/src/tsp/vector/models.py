from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


def _id() -> str:
    return uuid.uuid4().hex


class Collection(Base):
    __tablename__ = "vector_collections"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    embed_model: Mapped[str] = mapped_column(Text)
    dims: Mapped[int] = mapped_column(Integer)
    backend: Mapped[str] = mapped_column(Text, server_default="pgvector")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_vector_collections_org_name"),)


class Document(Base):
    """One source document. ``vector_chunks`` (raw SQL, see ``pg.py``) holds its pieces."""

    __tablename__ = "vector_documents"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(Text)
    collection_id: Mapped[str] = mapped_column(
        Text, ForeignKey("vector_collections.id", ondelete="CASCADE")
    )
    created_by: Mapped[str | None] = mapped_column(Text)
    source_uri: Mapped[str | None] = mapped_column(Text)  # "upload:<object key>" or a URL
    title: Mapped[str] = mapped_column(Text, server_default="")
    checksum: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("collection_id", "checksum", name="uq_vector_documents_checksum"),
        Index("ix_vector_documents_org", "org_id", "collection_id"),
    )
