from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


def _id() -> str:
    return uuid.uuid4().hex


class CrawlSource(Base):
    """A site an organization asked us to keep indexed. ``include`` and ``exclude`` are glob patterns
    matched against normalised URLs; crawling never leaves the seed's host. ``collection_id`` names
    a ``tsp.vector`` collection (no foreign key: the modules migrate independently)."""

    __tablename__ = "crawl_sources"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(Text)
    collection_id: Mapped[str] = mapped_column(Text)
    seed_url: Mapped[str] = mapped_column(Text)
    sitemap_url: Mapped[str | None] = mapped_column(Text)
    include: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    exclude: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    max_depth: Mapped[int] = mapped_column(Integer, server_default="2")
    max_pages: Mapped[int] = mapped_column(Integer, server_default="500")
    render: Mapped[str] = mapped_column(Text, server_default="never")  # never | auto | always
    refresh_hours: Mapped[int] = mapped_column(Integer, server_default="168")
    status: Mapped[str] = mapped_column(
        Text, server_default="active"
    )  # active | paused | error | deleting
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_crawl_sources_org", "org_id"),)


class CrawlPage(Base):
    __tablename__ = "crawl_pages"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text, ForeignKey("crawl_sources.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(Text)  # normalised
    final_url: Mapped[str | None] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer, server_default="0")
    content_hash: Mapped[str | None] = mapped_column(Text)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, server_default="queued"
    )  # queued | ok | not_modified | skipped_robots | error | gone
    error: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vector_document_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("source_id", "url", name="uq_crawl_pages_source_url"),
        Index("ix_crawl_pages_source_status", "source_id", "status"),
    )


__all__ = ["CrawlPage", "CrawlSource"]
