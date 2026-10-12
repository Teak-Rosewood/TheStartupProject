from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class _Row:
    """Every table is scoped to one organization and stamped on creation."""

    org_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Entity(_Row, Base):
    __tablename__ = "g_entities"
    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(Text)  # free text: person, org, product, document...
    name: Mapped[str] = mapped_column(Text)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    external_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    __table_args__ = (Index("ix_g_entities_org_kind", "org_id", "kind"),)


class Alias(_Row, Base):
    __tablename__ = "g_aliases"
    id: Mapped[uuid.UUID] = _pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("g_entities.id", ondelete="CASCADE"), index=True
    )
    alias: Mapped[str] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(Text)
    normalised: Mapped[str] = mapped_column(Text)  # lowercase, unaccented, single-spaced
    __table_args__ = (
        UniqueConstraint("entity_id", "normalised", name="uq_g_aliases_entity_normalised"),
        Index("ix_g_aliases_org_normalised", "org_id", "normalised"),
        Index(
            "ix_g_aliases_normalised_trgm",
            "normalised",
            postgresql_using="gin",
            postgresql_ops={"normalised": "gin_trgm_ops"},
        ),
    )


class Document(_Row, Base):
    __tablename__ = "g_documents"
    id: Mapped[uuid.UUID] = _pk()
    source_ref: Mapped[str | None] = mapped_column(Text)  # e.g. src_items.id
    vector_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text, server_default="")
    published_at: Mapped[date | None] = mapped_column(Date)
    kind: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(Text)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    __table_args__ = (Index("ix_g_documents_org_source", "org_id", "source_ref"),)


class Claim(_Row, Base):
    __tablename__ = "g_claims"
    id: Mapped[uuid.UUID] = _pk()
    text: Mapped[str] = mapped_column(Text)
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("g_entities.id", ondelete="CASCADE"))
    predicate: Mapped[str] = mapped_column(Text)
    object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("g_entities.id", ondelete="SET NULL")
    )
    object_value: Mapped[Any | None] = mapped_column(JSONB)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("g_documents.id", ondelete="SET NULL")
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    quote_start: Mapped[int | None] = mapped_column()
    quote_end: Mapped[int | None] = mapped_column()
    confidence: Mapped[float | None] = mapped_column(Float)
    extracted_by: Mapped[str | None] = mapped_column(Text)  # "model@prompt-version"
    status: Mapped[str] = mapped_column(Text, server_default="auto")  # auto | verified | rejected
    __table_args__ = (
        Index("ix_g_claims_org_subject", "org_id", "subject_id"),
        Index("ix_g_claims_document", "document_id"),
    )


class Link(_Row, Base):
    """A typed edge between two nodes (entity, document or claim) valid over a date range."""

    __tablename__ = "g_links"
    id: Mapped[uuid.UUID] = _pk()
    src_kind: Mapped[str] = mapped_column(Text)
    src_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    rel: Mapped[str] = mapped_column(Text)
    dst_kind: Mapped[str] = mapped_column(Text)
    dst_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    valid_from: Mapped[date | None] = mapped_column(Date)  # null = since forever
    valid_to: Mapped[date | None] = mapped_column(Date)  # null = still valid; exclusive
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    evidence_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("g_claims.id", ondelete="SET NULL")
    )
    __table_args__ = (
        Index("ix_g_links_src", "org_id", "src_kind", "src_id", "rel"),
        Index("ix_g_links_dst", "org_id", "dst_kind", "dst_id", "rel"),
    )


class Stance(_Row, Base):
    __tablename__ = "g_stances"
    id: Mapped[uuid.UUID] = _pk()
    holder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("g_entities.id", ondelete="CASCADE"))
    topic_key: Mapped[str] = mapped_column(Text)
    position: Mapped[float | None] = mapped_column(Numeric)  # -1..1
    label: Mapped[str | None] = mapped_column(Text)
    as_of: Mapped[date] = mapped_column(Date)
    evidence_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("g_claims.id", ondelete="SET NULL")
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (Index("ix_g_stances_org_topic", "org_id", "topic_key", "as_of"),)


class Series(_Row, Base):
    __tablename__ = "g_series"
    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(Text)
    frequency: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_g_series_org_key"),)


class Observation(_Row, Base):
    """One value for one period. A revision is a new row with a later ``vintage``; none is overwritten."""

    __tablename__ = "g_observations"
    series_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("g_series.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[date] = mapped_column(Date, primary_key=True)
    estimate_type: Mapped[str] = mapped_column(Text, primary_key=True, server_default="actual")
    vintage: Mapped[date] = mapped_column(
        Date, primary_key=True, server_default=func.current_date()
    )
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Numeric)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")


class Enrichment(_Row, Base):
    __tablename__ = "g_enrichments"
    id: Mapped[uuid.UUID] = _pk()
    target_kind: Mapped[str] = mapped_column(Text)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    enricher: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="running")  # running | done | failed
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "target_kind",
            "target_id",
            "enricher",
            "version",
            name="uq_g_enrichments_target",
        ),
    )
