"""Editorial backend: versions, language variants, review states, publish checks, scheduling."""

from pathlib import Path

from .api import Principal, build_public_router, build_router
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
from .lifecycle import LifecycleConfig, is_refreshable
from .models import (
    CheckResultRow,
    Correction,
    DisclosureTemplate,
    Item,
    Review,
    Variant,
    Version,
)
from .service import CmsError, CmsService, Diff, PublishBlocked

__all__ = [
    "AllVariantsReady",
    "CheckRegistry",
    "CheckResult",
    "CheckResultRow",
    "CmsError",
    "CmsService",
    "Correction",
    "Diff",
    "DisclosurePresent",
    "DisclosureTemplate",
    "Item",
    "LifecycleConfig",
    "Principal",
    "PublishBlocked",
    "PublishCheck",
    "Review",
    "RequiredFields",
    "Variant",
    "Version",
    "build_public_router",
    "build_router",
    "is_refreshable",
    "default_registry",
    "migrations_path",
    "register_jobs",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.cms"])``."""
    return Path(__file__).parent / "migrations" / "versions"
