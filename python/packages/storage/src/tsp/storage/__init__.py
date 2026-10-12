"""Direct-to-S3 uploads with limits, per-tenant prefixes and quotas."""

from pathlib import Path

from .api import build_router
from .models import StoredFile
from .service import (
    LocalObjectStore,
    ObjectStore,
    QuotaExceeded,
    S3ObjectStore,
    UploadPolicy,
    UploadRejected,
    Uploads,
    safe_filename,
)

__all__ = [
    "LocalObjectStore",
    "ObjectStore",
    "QuotaExceeded",
    "S3ObjectStore",
    "StoredFile",
    "UploadPolicy",
    "UploadRejected",
    "Uploads",
    "build_router",
    "migrations_path",
    "safe_filename",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.storage"])``."""
    return Path(__file__).parent / "migrations" / "versions"
