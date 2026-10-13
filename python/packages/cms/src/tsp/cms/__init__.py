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
from .complaints import ComplaintsConfig, ComplaintsDesk, Level
from .complaints_api import build_complaints_router, build_intake_router
from .compliance import register_complaints_with_compliance
from .jobs import register_jobs
from .lifecycle import LifecycleConfig, is_refreshable
from .models import (
    CheckResultRow,
    Complaint,
    ComplaintEvent,
    Correction,
    DisclosureTemplate,
    Item,
    Review,
    Variant,
    Version,
    Window,
)
from .service import CmsError, CmsService, Diff, PublishBlocked
from .windows import WindowRules, Windows, flag_key, sync_window_flags

__all__ = [
    "AllVariantsReady",
    "CheckRegistry",
    "CheckResult",
    "CheckResultRow",
    "CmsError",
    "CmsService",
    "Complaint",
    "ComplaintEvent",
    "ComplaintsConfig",
    "ComplaintsDesk",
    "Correction",
    "Diff",
    "DisclosurePresent",
    "DisclosureTemplate",
    "Item",
    "Level",
    "LifecycleConfig",
    "Principal",
    "PublishBlocked",
    "PublishCheck",
    "Review",
    "Window",
    "WindowRules",
    "Windows",
    "RequiredFields",
    "Variant",
    "Version",
    "build_complaints_router",
    "build_intake_router",
    "build_public_router",
    "build_router",
    "is_refreshable",
    "default_registry",
    "flag_key",
    "migrations_path",
    "register_complaints_with_compliance",
    "register_jobs",
    "sync_window_flags",
    "templates_dir",
]


def templates_dir() -> Path:
    """Pass this to ``Templates(...)`` so the notifier can render the complaint and review emails."""
    return Path(__file__).parent / "templates"


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.cms"])``."""
    return Path(__file__).parent / "migrations" / "versions"
