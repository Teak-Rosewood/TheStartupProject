"""Reader engagement: polls, read receipts, streaks, points, badges, onboarding."""

from pathlib import Path

from .api import build_router, build_submissions_router
from .compliance import register_with_compliance
from .config import BadgeRule, EngageConfig, ReadPolicy
from .jobs import purge_rejected, register_jobs
from .models import Badge, OnboardingStep, Point, Poll, Receipt, Response, Streak, Submission
from .moderation import (
    Blocklist,
    Draft,
    FactCheckHook,
    Length,
    LlmRubric,
    Moderator,
    Verdict,
    build_workflow,
    default_moderators,
    push_prompts,
)
from .onboarding import Onboarding
from .points import Points
from .polls import PollError, Polls
from .receipts import Receipts, TooManyPings
from .service import Engage, ReadOutcome
from .streaks import Streaks, StreakState, previous_week_key, week_key
from .submissions import APPEAL_CATEGORY, Eligibility, SubmissionError, Submissions

__all__ = [
    "APPEAL_CATEGORY",
    "Badge",
    "Blocklist",
    "Draft",
    "Eligibility",
    "FactCheckHook",
    "Length",
    "LlmRubric",
    "Moderator",
    "Submission",
    "SubmissionError",
    "Submissions",
    "Verdict",
    "build_submissions_router",
    "build_workflow",
    "default_moderators",
    "push_prompts",
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
    "purge_rejected",
    "register_jobs",
    "register_with_compliance",
    "week_key",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.engage"])``."""
    return Path(__file__).parent / "migrations" / "versions"
