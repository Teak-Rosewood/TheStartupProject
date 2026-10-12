import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tsp.db import normalize_url

BASE_URL = os.environ.get(
    "TSP_TEST_DATABASE_URL", "postgresql://postgres:test@127.0.0.1:55432/test"
)
# A database of its own: other suites drop shared tables and `alembic_version` while they run.
URL = BASE_URL.rsplit("/", 1)[0] + "/tsp_graph_test"
TABLES = (
    "g_enrichments, g_observations, g_series, g_stances, g_links, g_claims, g_documents, "
    "g_aliases, g_entities, alembic_version"
)
ORG = "org-1"
OTHER_ORG = "org-2"


async def _ensure_database() -> None:
    engine = create_async_engine(normalize_url(BASE_URL), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as c:
            exists = (
                await c.execute(text("SELECT 1 FROM pg_database WHERE datname = 'tsp_graph_test'"))
            ).first()
            if not exists:
                await c.execute(text("CREATE DATABASE tsp_graph_test TEMPLATE template0"))
    finally:
        await engine.dispose()
