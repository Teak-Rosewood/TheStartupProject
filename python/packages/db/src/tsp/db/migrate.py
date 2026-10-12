"""Run Alembic against an async engine. Apps own their ``migrations/`` folder; this owns the plumbing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from alembic import command, context
from alembic.config import Config
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from .engine import normalize_url

# Serializes concurrent runners (rolling deploys, several replicas): the second waits, then no-ops.
ADVISORY_LOCK_KEY = 727274


def upgrade(url: str, script_location: str | Path, revision: str = "head") -> None:
    """Apply migrations. Called by ``tsp-db migrate`` (the CI one-off task)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", normalize_url(url).replace("%", "%%"))
    command.upgrade(cfg, revision)


def run_alembic_env(target_metadata: Any = None) -> None:
    """Body of an app's ``migrations/env.py``: ``run_alembic_env(Base.metadata)``."""
    config = context.config
    if context.is_offline_mode():
        context.configure(
            url=config.get_main_option("sqlalchemy.url"),
            target_metadata=target_metadata,
            literal_binds=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    def do_run(connection: Connection) -> None:
        locked = connection.dialect.name == "postgresql"
        if locked:
            connection.execute(text("SELECT pg_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY})
            connection.commit()
        try:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
            connection.commit()
        finally:
            if locked:
                connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
                connection.commit()

    async def go() -> None:
        engine = async_engine_from_config(
            config.get_section(config.config_ini_section) or {},
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        async with engine.connect() as conn:
            await conn.run_sync(do_run)
        await engine.dispose()

    asyncio.run(go())
