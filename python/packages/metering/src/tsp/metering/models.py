from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


class UsageRecord(Base):
    """One metered call. Append-only; billing reads the credit ledger, this is for dashboards and disputes."""

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    org_id: Mapped[str] = mapped_column(Text)
    api_key_id: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # e.g. ai.chat
    model: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)
    units_in: Mapped[int] = mapped_column(Integer, server_default="0")
    units_out: Mapped[int] = mapped_column(Integer, server_default="0")
    credits: Mapped[int] = mapped_column(Integer, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, server_default="ok")  # ok | error | blocked
    request_id: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")

    __table_args__ = (
        Index("ix_usage_records_org_ts", "org_id", "ts"),
        Index("ix_usage_records_kind_ts", "kind", "ts"),
    )
