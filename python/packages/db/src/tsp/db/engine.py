from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def normalize_url(url: str) -> str:
    """Accept ``postgres://`` / ``postgresql://`` (what AWS and most tools hand out) and pick asyncpg."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    return url


class Database:
    """Engine + session factory. One per process; create at startup, ``dispose()`` at shutdown."""

    def __init__(self, url: str, *, pool_size: int = 5, echo: bool = False):
        self.engine: AsyncEngine = create_async_engine(
            normalize_url(url),
            pool_size=pool_size,
            max_overflow=2,
            pool_pre_ping=True,  # survives Postgres restarts and host replacement
            echo=echo,
        )
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Session that commits on success and rolls back on error."""
        async with self._sessions() as s:
            try:
                yield s
                await s.commit()
            except BaseException:
                await s.rollback()
                raise

    async def ping(self) -> bool:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()
