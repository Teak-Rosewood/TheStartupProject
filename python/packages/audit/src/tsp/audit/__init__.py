"""Append-only audit log: who did what to what, when, from where."""

from pathlib import Path

from .log import Actor, AuditEntry, client_ip, record
from .models import AuditLog

__all__ = ["Actor", "AuditEntry", "AuditLog", "client_ip", "migrations_path", "record"]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.audit"])``."""
    return Path(__file__).parent / "migrations" / "versions"
