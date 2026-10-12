from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base

_TS = DateTime(timezone=True)


def _id() -> str:
    return uuid.uuid4().hex


class Email(Base):
    """The outbox: one row per message, written in the same transaction as whatever caused it."""

    __tablename__ = "emails"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=_id)
    to_addr: Mapped[str] = mapped_column(Text)
    template: Mapped[str] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    category: Mapped[str] = mapped_column(Text)  # transactional | marketing
    status: Mapped[str] = mapped_column(
        Text, server_default="queued"
    )  # queued | sent | suppressed | failed
    provider_message_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(_TS)

    __table_args__ = (Index("ix_emails_to_addr", "to_addr"),)


class Suppression(Base):
    """Addresses we must not mail: hard bounces, complaints, and marketing opt-outs."""

    __tablename__ = "email_suppressions"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    reason: Mapped[str] = mapped_column(Text)  # bounce | complaint | unsubscribed
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class EmailPreference(Base):
    """Marketing consent per address. No row means no consent: marketing is opt-in."""

    __tablename__ = "email_preferences"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    marketing: Mapped[bool] = mapped_column(Boolean, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    user_sub: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(Text)  # android | ios | web
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())

    __table_args__ = (Index("ix_device_tokens_user_sub", "user_sub"),)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_sub: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, server_default="")
    url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(_TS, server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(_TS)

    __table_args__ = (Index("ix_notifications_user_sub_id", "user_sub", "id"),)
