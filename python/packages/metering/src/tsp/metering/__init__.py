"""Usage records and credit pricing for metered features."""

from pathlib import Path

from .api import build_router
from .models import UsageRecord
from .service import Rates, estimate_tokens, price_credits, record_usage, summary

__all__ = [
    "Rates",
    "UsageRecord",
    "build_router",
    "estimate_tokens",
    "migrations_path",
    "price_credits",
    "record_usage",
    "summary",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.metering"])``."""
    return Path(__file__).parent / "migrations" / "versions"
