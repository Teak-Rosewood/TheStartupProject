"""External data sources and connectors: strategy fallback, cursors, health checks."""

from pathlib import Path

from .api import build_router
from .checks import CheckResult, SourceChecks
from .config import SourcesConfig
from .connector import (
    Connector,
    ConnectorContext,
    Item,
    PaidBudget,
    SourceInfo,
    Strategy,
    StrategyFailed,
)
from .jobs import register_jobs
from .models import Check, Cursor, Run, Source
from .registry import connectors, get_connector, register_connector
from .runner import ReconcileReport, RunResult, SourceDisabled, SourceRunner, StreamResult
from .store import LocalRawStore, RawStore, S3RawStore
from .strategies import (
    ApiPage,
    BulkFileStrategy,
    Entry,
    HttpListStrategy,
    JsonApiStrategy,
    Listing,
    PaidApiStrategy,
    SitemapStrategy,
)

__all__ = [
    "ApiPage",
    "BulkFileStrategy",
    "Check",
    "CheckResult",
    "Connector",
    "ConnectorContext",
    "Cursor",
    "Entry",
    "HttpListStrategy",
    "Item",
    "JsonApiStrategy",
    "Listing",
    "LocalRawStore",
    "PaidApiStrategy",
    "PaidBudget",
    "RawStore",
    "ReconcileReport",
    "Run",
    "RunResult",
    "S3RawStore",
    "SitemapStrategy",
    "Source",
    "SourceChecks",
    "SourceDisabled",
    "SourceInfo",
    "SourceRunner",
    "SourcesConfig",
    "Strategy",
    "StrategyFailed",
    "StreamResult",
    "build_router",
    "connectors",
    "get_connector",
    "migrations_path",
    "register_connector",
    "register_jobs",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.sources"])``."""
    return Path(__file__).parent / "migrations" / "versions"
