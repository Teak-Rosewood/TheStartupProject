from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


class Flag(Base):
    """``enabled`` is the master switch. Beyond it: explicit allow/deny lists, plan targeting and a
    percentage rollout. Keys starting with ``kill:`` are kill switches (see ``Flags.killed``)."""

    __tablename__ = "flags"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text, server_default="")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    rollout_percent: Mapped[int] = mapped_column(Integer, server_default="100")
    rules: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default="{}"
    )  # users, orgs, plans, deny_users, deny_orgs
    client_visible: Mapped[bool] = mapped_column(Boolean, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[str | None] = mapped_column(Text)
