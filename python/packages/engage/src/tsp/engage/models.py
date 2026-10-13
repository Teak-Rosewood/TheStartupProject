from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
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


class Poll(Base):
    __tablename__ = "en_polls"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[str] = mapped_column(Text)
    item_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # scale1d | scale2d
    # {"x": {"label": "...", "min": 0, "max": 10, "min_label": "...", "max_label": "..."}, "y": {...},
    #  "before_required": true}
    axes: Mapped[dict[str, Any]] = mapped_column(JSONB)
    opens_at: Mapped[datetime | None] = mapped_column(_TS)
    closes_at: Mapped[datetime | None] = mapped_column(_TS)

    __table_args__ = (Index("ix_en_polls_item", "org_id", "item_id"),)


class Response(Base):
    __tablename__ = "en_responses"

    poll_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("en_polls.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    phase: Mapped[str] = mapped_column(Text, primary_key=True)  # before | after
    x: Mapped[float] = mapped_column(Float)
    y: Mapped[float | None] = mapped_column(Float)
    at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class Receipt(Base):
    __tablename__ = "en_receipts"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text)
    version_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(_TS)
    expected_seconds: Mapped[int | None] = mapped_column(Integer)
    dwell_seconds: Mapped[int] = mapped_column(Integer, server_default="0")
    max_scroll: Mapped[float] = mapped_column(Float, server_default="0")
    reached_end: Mapped[bool] = mapped_column(Boolean, server_default="false")
    served_full: Mapped[bool] = mapped_column(Boolean, server_default="true")


class Streak(Base):
    __tablename__ = "en_streaks"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    period: Mapped[str] = mapped_column(Text, primary_key=True, server_default="week")
    current: Mapped[int] = mapped_column(Integer, server_default="0")
    best: Mapped[int] = mapped_column(Integer, server_default="0")
    last_period_key: Mapped[str | None] = mapped_column(Text)  # the last week the goal was met
    progress_key: Mapped[str | None] = mapped_column(Text)  # the week being counted now
    progress_count: Mapped[int] = mapped_column(Integer, server_default="0")


class Point(Base):
    __tablename__ = "en_points"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text)
    org_id: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer)
    ref_kind: Mapped[str] = mapped_column(Text, server_default="")
    ref_id: Mapped[str] = mapped_column(Text, server_default="")
    at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "user_id", "org_id", "reason", "ref_kind", "ref_id", name="uq_en_points_ref"
        ),
        Index("ix_en_points_user", "user_id", "org_id"),
    )


class Badge(Base):
    __tablename__ = "en_badges"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    badge: Mapped[str] = mapped_column(Text, primary_key=True)
    at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class OnboardingStep(Base):
    __tablename__ = "en_onboarding"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    step: Mapped[str] = mapped_column(Text, primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
