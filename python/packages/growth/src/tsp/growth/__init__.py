"""Waitlist, first-party events, attribution, referrals and business metrics."""

from pathlib import Path

from . import events, metrics, referrals
from .leads import Captcha, Leads
from .models import Event, Lead, Referral, ReferralCode

__all__ = [
    "Captcha",
    "Event",
    "Lead",
    "Leads",
    "Referral",
    "ReferralCode",
    "events",
    "metrics",
    "migrations_path",
    "referrals",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.growth"])``."""
    return Path(__file__).parent / "migrations" / "versions"
