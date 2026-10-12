from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base

_NOW = func.now()


class Source(Base):
    """One external source. ``strategies`` is the ordered list of methods to try (configuration,
    not code); ``org_id`` is null for platform-wide sources."""

    __tablename__ = "src_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    tier: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    languages: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    licence: Mapped[str | None] = mapped_column(Text)
    terms_url: Mapped[str | None] = mapped_column(Text)
    robots_ok: Mapped[bool] = mapped_column(Boolean, server_default="true")
    terms_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    allowed_uses: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    strategies: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    schedule: Mapped[str | None] = mapped_column(Text)
    rate_limit_rps: Mapped[float] = mapped_column(Float, server_default="1.0")
    owner: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    paid_cap_monthly: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    notes: Mapped[str | None] = mapped_column(Text)


class Cursor(Base):
    __tablename__ = "src_cursors"

    source_id: Mapped[int] = mapped_column(
        ForeignKey("src_sources.id", ondelete="CASCADE"), primary_key=True
    )
    stream: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class Run(Base):
    __tablename__ = "src_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("src_sources.id", ondelete="CASCADE"))
    stream: Mapped[str] = mapped_column(Text)
    strategy_used: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)  # ok | partial | failed
    items_new: Mapped[int] = mapped_column(Integer, server_default="0")
    items_updated: Mapped[int] = mapped_column(Integer, server_default="0")
    errors: Mapped[int] = mapped_column(Integer, server_default="0")
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), server_default="0")
    manifest_uri: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)


class Item(Base):
    __tablename__ = "src_items"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("src_sources.id", ondelete="CASCADE"))
    stream: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(Text)
    raw_uri: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")


class Check(Base):
    __tablename__ = "src_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("src_sources.id", ondelete="CASCADE"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    robots_ok: Mapped[bool | None] = mapped_column(Boolean)  # null: robots.txt unreadable
    terms_hash: Mapped[str | None] = mapped_column(Text)
    reachable: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
