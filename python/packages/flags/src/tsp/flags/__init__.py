"""Feature flags and kill switches."""

from pathlib import Path

from .api import build_router, require_enabled, require_not_killed
from .models import Flag
from .service import FlagContext, Flags, FlagUpdate, bucket

__all__ = [
    "Flag",
    "FlagContext",
    "FlagUpdate",
    "Flags",
    "bucket",
    "build_router",
    "migrations_path",
    "require_enabled",
    "require_not_killed",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.flags"])``."""
    return Path(__file__).parent / "migrations" / "versions"
