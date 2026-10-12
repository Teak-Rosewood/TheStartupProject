from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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


class Run(Base):
    __tablename__ = "pl_runs"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[str] = mapped_column(Text)
    workflow: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)
    input: Mapped[Any] = mapped_column(JSONB)
    output: Mapped[Any | None] = mapped_column(JSONB)
    # queued | running | waiting | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric)
    spent_usd: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    started_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(_TS)
    error: Mapped[str | None] = mapped_column(Text)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pl_runs.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_pl_runs_org_started", "org_id", "started_at"),)


class Step(Base):
    __tablename__ = "pl_steps"

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pl_runs.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, server_default="1")
    # queued | running | waiting (human) | succeeded | failed
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    input: Mapped[Any | None] = mapped_column(JSONB)
    output: Mapped[Any | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(Text)
    prompt_key: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(_TS)
    finished_at: Mapped[datetime | None] = mapped_column(_TS)
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("run_id", "name", "attempt", name="uq_pl_steps_attempt"),)


class Prompt(Base):
    __tablename__ = "pl_prompts"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    body: Mapped[str] = mapped_column(Text)
    model_hint: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    notes: Mapped[str | None] = mapped_column(Text)


class PromptActive(Base):
    __tablename__ = "pl_prompt_active"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)


class Event(Base):
    """Trace: what happened in a run, in order."""

    __tablename__ = "pl_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pl_runs.id", ondelete="CASCADE"))
    step: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    kind: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")

    __table_args__ = (Index("ix_pl_events_run", "run_id", "id"),)
