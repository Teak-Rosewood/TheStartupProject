from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


class Policy(Base):
    """How much a reader gets free: ``free_items`` different items per ``window_days``."""

    __tablename__ = "pw_policies"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: uuid.uuid4().hex)
    org_id: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    free_items: Mapped[int] = mapped_column(Integer)
    window_days: Mapped[int] = mapped_column(Integer)
    wall: Mapped[str] = mapped_column(Text, server_default="hard")
    applies_to: Mapped[list[str]] = mapped_column(
        JSONB, server_default="[]"
    )  # item types; empty = all
    require_account_after: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class Read(Base):
    __tablename__ = "pw_reads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(Text)
    meter_id: Mapped[str | None] = mapped_column(Text)
    item_id: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Gift(Base):
    __tablename__ = "pw_gifts"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text)
    item_id: Mapped[str] = mapped_column(Text)
    giver_user_id: Mapped[str] = mapped_column(Text)
    max_uses: Mapped[int] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Credit(Base):
    __tablename__ = "pw_credits"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    org_id: Mapped[str] = mapped_column(Text, primary_key=True)
    balance: Mapped[int] = mapped_column(Integer, server_default="0")


class Unlock(Base):
    __tablename__ = "pw_unlocks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(Text)
    org_id: Mapped[str] = mapped_column(Text)
    item_id: Mapped[str] = mapped_column(Text)
    via: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("subject", "org_id", "item_id", name="uq_pw_unlocks"),)
