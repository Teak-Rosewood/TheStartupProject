"""FastAPI app factory wiring tsp.auth / db / cache / nosql with production defaults."""

from .app import create_app
from .deps import current_user, db_session, get_cache, get_table, require_groups
from .logging import configure_logging
from .settings import Settings

__all__ = [
    "Settings",
    "configure_logging",
    "create_app",
    "current_user",
    "db_session",
    "get_cache",
    "get_table",
    "require_groups",
]
