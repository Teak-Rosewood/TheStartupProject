from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base

_TS = DateTime(timezone=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    queue: Mapped[str] = mapped_column(
        Text, server_default="default"
    )  # workers claim only the queues they were started for
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(
        Text, server_default="queued"
    )  # queued | running | done | dead
    run_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="5")
    locked_at: Mapped[datetime | None] = mapped_column(_TS)
    locked_by: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    unique_key: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(_TS)

    __table_args__ = (
        Index("ix_jobs_status_run_at", "status", "run_at"),
        Index("ix_jobs_queue_status_run_at", "queue", "status", "run_at"),
    )


class CronJob(Base):
    __tablename__ = "cron_jobs"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    schedule: Mapped[str] = mapped_column(Text)
    job_name: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    next_run_at: Mapped[datetime] = mapped_column(_TS)
    last_run_at: Mapped[datetime | None] = mapped_column(_TS)
