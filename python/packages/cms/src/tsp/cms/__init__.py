"""Editorial backend: versions, language variants, review states, publish checks, scheduling."""

from pathlib import Path

from .api import Principal, build_router
from .checks import (
    AllVariantsReady,
    CheckRegistry,
    CheckResult,
    DisclosurePresent,
    PublishCheck,
    RequiredFields,
    default_registry,
)
from .jobs import register_jobs
from .models import CheckResultRow, DisclosureTemplate, Item, Variant, Version
from .service import CmsError, CmsService, Diff, PublishBlocked

__all__ = [
    "AllVariantsReady",
    "CheckRegistry",
    "CheckResult",
    "CheckResultRow",
    "CmsError",
    "CmsService",
    "Diff",
    "DisclosurePresent",
    "DisclosureTemplate",
    "Item",
    "Principal",
    "PublishBlocked",
    "PublishCheck",
    "RequiredFields",
    "Variant",
    "Version",
    "build_router",
    "default_registry",
    "migrations_path",
    "register_jobs",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.cms"])``."""
    return Path(__file__).parent / "migrations" / "versions"
