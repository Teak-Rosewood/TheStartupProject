from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from tsp.db import Base


class StoredFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: uuid.uuid4().hex)
    org_id: Mapped[str] = mapped_column(Text)
    user_sub: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text, unique=True)
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(
        BigInteger
    )  # declared at upload time; checked against the object on completion
    status: Mapped[str] = mapped_column(
        Text, server_default="pending"
    )  # pending | ready | rejected | deleted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_files_org_id_status", "org_id", "status"),)
