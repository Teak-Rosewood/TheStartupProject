"""Email, push, SMS and in-app notifications."""

from pathlib import Path

from .compliance import register as register_with_compliance
from .events import poll_ses_events
from .inapp import forget_tokens, mark_read, notify_user, register_device, unread_count, user_tokens
from .mailer import (
    MARKETING,
    SEND_JOB,
    TRANSACTIONAL,
    EmailTransport,
    Notifier,
    Outgoing,
    SesTransport,
    TransportError,
    register_jobs,
)
from .models import DeviceToken, Email, EmailPreference, Notification, Suppression
from .push import FcmPush, PushResult
from .sms import SnsSms
from .templating import Rendered, Templates

__all__ = [
    "MARKETING",
    "SEND_JOB",
    "TRANSACTIONAL",
    "DeviceToken",
    "Email",
    "EmailPreference",
    "EmailTransport",
    "FcmPush",
    "Notification",
    "Notifier",
    "Outgoing",
    "PushResult",
    "Rendered",
    "SesTransport",
    "SnsSms",
    "Suppression",
    "Templates",
    "TransportError",
    "forget_tokens",
    "register_jobs",
    "mark_read",
    "migrations_path",
    "notify_user",
    "poll_ses_events",
    "register_device",
    "register_with_compliance",
    "unread_count",
    "user_tokens",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.notify"])``."""
    return Path(__file__).parent / "migrations" / "versions"
