from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base

_TS = DateTime(timezone=True)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class Item(Base):
    __tablename__ = "cms_items"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)  # free text: article, recipe, guide...
    slug: Mapped[str] = mapped_column(Text)
    primary_lang: Mapped[str] = mapped_column(Text, server_default="en")
    # draft | in_review | approved | scheduled | published | unpublished
    state: Mapped[str] = mapped_column(Text, server_default="draft")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cms_versions.id", use_alter=True, name="fk_cms_items_current"),
    )
    published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cms_versions.id", use_alter=True, name="fk_cms_items_published"),
    )
    publish_at: Mapped[datetime | None] = mapped_column(_TS)
    published_at: Mapped[datetime | None] = mapped_column(_TS)
    lifecycle: Mapped[str] = mapped_column(Text, server_default="open")  # open | living | frozen
    review_due_at: Mapped[datetime | None] = mapped_column(_TS)
    frozen_at: Mapped[datetime | None] = mapped_column(_TS)
    frozen_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")

    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_cms_items_org_slug"),
        Index("ix_cms_items_due", "state", "publish_at"),
    )


class Version(Base):
    __tablename__ = "cms_versions"

    id: Mapped[uuid.UUID] = _pk()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cms_items.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    lang: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, server_default="")
    body: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")  # list of blocks
    summary: Mapped[str] = mapped_column(Text, server_default="")
    disclosures: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    author_kind: Mapped[str] = mapped_column(Text, server_default="human")  # human | ai | mixed
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # tsp.pipeline run
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    change_note: Mapped[str] = mapped_column(Text, server_default="")

    __table_args__ = (UniqueConstraint("item_id", "number", name="uq_cms_versions_number"),)


class Variant(Base):
    """The latest version of an item in one language, and whether that language is ready to ship."""

    __tablename__ = "cms_variants"

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cms_items.id", ondelete="CASCADE"), primary_key=True
    )
    lang: Mapped[str] = mapped_column(Text, primary_key=True)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cms_versions.id"))
    state: Mapped[str] = mapped_column(Text, server_default="draft")  # draft | ready | published


class CheckResultRow(Base):
    __tablename__ = "cms_check_results"

    id: Mapped[uuid.UUID] = _pk()
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cms_versions.id", ondelete="CASCADE"), index=True
    )
    check: Mapped[str] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(Boolean)
    blocking: Mapped[bool] = mapped_column(Boolean, server_default="true")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    ran_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class DisclosureTemplate(Base):
    """Standard wording for a disclosure block, per organization and language."""

    __tablename__ = "cms_disclosure_templates"

    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(
        Text, primary_key=True
    )  # ai_assistance | methodology | conflict
    lang: Mapped[str] = mapped_column(Text, primary_key=True)
    text: Mapped[str] = mapped_column(Text)


class Correction(Base):
    """One entry in an item's public log of corrections, clarifications and updates."""

    __tablename__ = "cms_corrections"

    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity())
    org_id: Mapped[str] = mapped_column(Text)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cms_items.id", ondelete="CASCADE"))
    version_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cms_versions.id")
    )
    version_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cms_versions.id")
    )
    kind: Mapped[str] = mapped_column(Text)  # correction | clarification | update
    public_note: Mapped[str] = mapped_column(Text)
    internal_note: Mapped[str] = mapped_column(Text, server_default="")  # never leaves the CMS
    complaint_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class Review(Base):
    """A due review of a published item: keep it living, or freeze it. Open until decided."""

    __tablename__ = "cms_reviews"

    id: Mapped[uuid.UUID] = _pk()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cms_items.id", ondelete="CASCADE"))
    due_at: Mapped[datetime] = mapped_column(_TS)
    notified_at: Mapped[datetime | None] = mapped_column(_TS)
    decided_at: Mapped[datetime | None] = mapped_column(_TS)
    decision: Mapped[str | None] = mapped_column(Text)  # living | frozen
    decided_by: Mapped[str | None] = mapped_column(Text)


class Complaint(Base):
    __tablename__ = "cms_complaints"

    id: Mapped[uuid.UUID] = _pk()
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    org_id: Mapped[str] = mapped_column(Text)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cms_items.id", ondelete="SET NULL")
    )
    complainant_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(_TS)
    ack_due_at: Mapped[datetime | None] = mapped_column(_TS)
    resolve_due_at: Mapped[datetime | None] = mapped_column(_TS)
    # received | acknowledged | in_progress | resolved | rejected | escalated
    status: Mapped[str] = mapped_column(Text, server_default="received")
    level: Mapped[int] = mapped_column(Integer, server_default="1")
    assigned_to: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(Text)
    correction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cms_corrections.id")
    )
    closed_at: Mapped[datetime | None] = mapped_column(_TS)
    ack_warned_at: Mapped[datetime | None] = mapped_column(_TS)
    resolve_warned_at: Mapped[datetime | None] = mapped_column(_TS)

    @property
    def reference(self) -> str:
        """What the complainant quotes: short, stable, and not the row id."""
        return f"C-{self.received_at.year}-{self.seq:06d}"


class ComplaintEvent(Base):
    """Everything that happened to a complaint, in order. Internal: never shown to the complainant."""

    __tablename__ = "cms_complaint_events"

    id: Mapped[uuid.UUID] = _pk()
    complaint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cms_complaints.id", ondelete="CASCADE")
    )
    at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    actor: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, server_default="")


class Window(Base):
    """A period (optionally one region) in which extra publishing rules apply, such as an election."""

    __tablename__ = "cms_windows"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(_TS)
    ends_at: Mapped[datetime] = mapped_column(_TS)
    region: Mapped[str | None] = mapped_column(Text)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")

    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_cms_windows_name"),)
