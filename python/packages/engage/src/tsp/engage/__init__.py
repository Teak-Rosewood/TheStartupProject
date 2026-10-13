"""Reader engagement: polls, read receipts, streaks, points, badges, onboarding."""

from pathlib import Path

from .api import build_router
from .compliance import register_with_compliance
from .config import BadgeRule, EngageConfig, ReadPolicy
from .models import Badge, OnboardingStep, Point, Poll, Receipt, Response, Streak
from .onboarding import Onboarding
from .points import Points
from .polls import PollError, Polls
from .receipts import Receipts, TooManyPings
from .service import Engage, ReadOutcome
from .streaks import Streaks, StreakState, previous_week_key, week_key

__all__ = [
    "Badge",
    "BadgeRule",
    "Engage",
    "EngageConfig",
    "Onboarding",
    "OnboardingStep",
    "Point",
    "Points",
    "Poll",
    "PollError",
    "Polls",
    "ReadOutcome",
    "ReadPolicy",
    "Receipt",
    "Receipts",
    "Response",
    "Streak",
    "StreakState",
    "Streaks",
    "TooManyPings",
    "build_router",
    "migrations_path",
    "previous_week_key",
    "register_with_compliance",
    "week_key",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.engage"])``."""
    return Path(__file__).parent / "migrations" / "versions"
