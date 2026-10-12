from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
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
