"""Consent by document version, data export and deletion, legal document rendering."""

from pathlib import Path

from .api import build_router, make_guards
from .documents import Document, Manifest
from .identity import CognitoIdentity, IdentityProvider
from .models import Consent, DataRequest
from .service import Compliance, ConsentError
from .storage import ExportStore, LocalExportStore, S3ExportStore

__all__ = [
    "CognitoIdentity",
    "Compliance",
    "Consent",
    "ConsentError",
    "DataRequest",
    "Document",
    "ExportStore",
    "IdentityProvider",
    "LocalExportStore",
    "Manifest",
    "S3ExportStore",
    "build_router",
    "make_guards",
    "migrations_path",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.compliance"])``."""
    return Path(__file__).parent / "migrations" / "versions"
