"""Users, organizations, memberships, invites and customer API keys."""

from pathlib import Path

from .models import ApiKey, Invite, Membership, Org, User
from .service import (
    Accounts,
    AccountsError,
    ApiKeyInfo,
    ForbiddenError,
    NotFoundError,
    OwnershipError,
    has_role,
)

__all__ = [
    "Accounts",
    "AccountsError",
    "ApiKey",
    "ApiKeyInfo",
    "ForbiddenError",
    "Invite",
    "Membership",
    "NotFoundError",
    "Org",
    "OwnershipError",
    "User",
    "has_role",
    "migrations_path",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.accounts"])``."""
    return Path(__file__).parent / "migrations" / "versions"
