from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base

_TS = DateTime(timezone=True)


class Lead(Base):
    """A waitlist signup. Not a contact we may mail until ``confirmed_at`` is set (double opt-in)."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    source: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    consent_text: Mapped[str] = mapped_column(Text)  # the exact wording the person agreed to
    consent_ip_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(_TS)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(_TS)


class Event(Base):
    """First-party product analytics. Properties never hold personal data (see ``events.sanitize``)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    name: Mapped[str] = mapped_column(Text)
    anonymous_id: Mapped[str] = mapped_column(Text)
    user_sub: Mapped[str | None] = mapped_column(Text)
    org_id: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")

    __table_args__ = (
        Index("ix_events_name_ts", "name", "ts"),
        Index("ix_events_user_sub_ts", "user_sub", "ts"),
        Index("ix_events_anonymous_id", "anonymous_id"),
    )


class ReferralCode(Base):
    __tablename__ = "referral_codes"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_sub: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class Referral(Base):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text)
    referred_sub: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("referred_sub", name="uq_referrals_referred_sub"),
        Index("ix_referrals_code", "code"),
    )
