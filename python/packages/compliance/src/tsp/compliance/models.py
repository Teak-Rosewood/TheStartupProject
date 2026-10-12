from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


class Consent(Base):
    """One acceptance of one document version by one user. Never updated; latest row wins."""

    __tablename__ = "consents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_sub: Mapped[str] = mapped_column(Text)
    document: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ip: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default="app")

    __table_args__ = (
        Index("ix_consents_user_document_accepted", "user_sub", "document", "accepted_at"),
    )


class DataRequest(Base):
    """A user's export or deletion request. Kept after completion as proof we responded."""

    __tablename__ = "data_requests"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: uuid.uuid4().hex)
    user_sub: Mapped[str] = mapped_column(Text, index=True)
    kind: Mapped[str] = mapped_column(Text)  # export | delete
    status: Mapped[str] = mapped_column(Text)  # pending | completed | failed | cancelled
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_key: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
