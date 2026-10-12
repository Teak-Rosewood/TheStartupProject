from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


def _id() -> str:
    return uuid.uuid4().hex


class TopicRow(Base):
    __tablename__ = "tr_topics"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    langs: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    regions: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")

    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_tr_topics_org_key"),)


class SignalRow(Base):
    """One measurement. ``topic_id`` is null for signals about something no topic covers yet (an
    emerging term); the term is then in ``attrs['label']``."""

    __tablename__ = "tr_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(Text)
    topic_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("tr_topics.id", ondelete="CASCADE")
    )
    signal: Mapped[str] = mapped_column(Text)
    region: Mapped[str] = mapped_column(Text, server_default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[Decimal] = mapped_column(Numeric)
    baseline: Mapped[Decimal | None] = mapped_column(Numeric)
    attrs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")

    __table_args__ = (Index("ix_tr_signals_org_at", "org_id", "at"),)


class CandidateRow(Base):
    __tablename__ = "tr_candidates"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    org_id: Mapped[str] = mapped_column(Text)
    day: Mapped[date] = mapped_column(Date)
    topic_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("tr_topics.id", ondelete="SET NULL")
    )
    label: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    components: Mapped[dict[str, float]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(Text, server_default="new")  # new | picked | dismissed
    picked_by: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("org_id", "day", "label", name="uq_tr_candidates_org_day_label"),
    )
