"""Metered and hard paywalls."""

from pathlib import Path

from .api import build_router, visitor_from_request
from .compliance import register_with_compliance
from .crawlers import Resolver, SystemResolver, paywall_jsonld, verify_crawler
from .jobs import register_jobs
from .meter import COOKIE, device_key, new_meter_id, sign, verify
from .models import Credit, Gift, Policy, Read, Unlock
from .service import Decision, InsufficientCredits, Item, Paywall, Visitor

__all__ = [
    "COOKIE",
    "Credit",
    "Decision",
    "Gift",
    "InsufficientCredits",
    "Item",
    "Paywall",
    "Policy",
    "Read",
    "Resolver",
    "SystemResolver",
    "Unlock",
    "Visitor",
    "build_router",
    "device_key",
    "migrations_path",
    "new_meter_id",
    "paywall_jsonld",
    "register_jobs",
    "register_with_compliance",
    "sign",
    "verify",
    "verify_crawler",
    "visitor_from_request",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.paywall"])``."""
    return Path(__file__).parent / "migrations" / "versions"
