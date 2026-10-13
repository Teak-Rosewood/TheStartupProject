from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

DEFAULT_POINTS = {
    "read": 10,
    "poll": 5,
    "streak_week": 20,
    "onboarding": 10,
    "submission_accepted": 15,
    "submission_evidence": 10,  # bonus when the substance score is high
}


@dataclass(frozen=True)
class ReadPolicy:
    """When does an item count as read. Dwell time is capped by the real time since the page was
    served, so a client cannot claim minutes it did not spend."""

    min_dwell_ratio: float = 0.4  # of the expected reading time
    require_end: bool = True  # must have scrolled to the end
    default_expected_seconds: int = 90


@dataclass(frozen=True)
class BadgeRule:
    """``reason``+``count``: that many awards for the reason. ``streak``: that many weeks in a row."""

    name: str
    reason: str | None = None
    count: int | None = None
    streak: int | None = None


@dataclass(frozen=True)
class EngageConfig:
    timezone: str = "Asia/Kolkata"  # weeks start on Monday in this zone
    weekly_goal: int = 1  # items read per week to keep the streak
    min_k: int = 20  # aggregates are hidden below this many responses
    bins: int = 10
    points: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_POINTS))
    read_policy: ReadPolicy = field(default_factory=ReadPolicy)
    require_subscription: bool = (
        True  # submitting needs an active subscription (see Submissions.entitled)
    )
    evidence_threshold: float = 0.7  # substance score that earns the evidence bonus
    required_onboarding: tuple[str, ...] = ("welcome", "interests")
    badges: tuple[BadgeRule, ...] = (
        BadgeRule("first_read", reason="read", count=1),
        BadgeRule("regular", reason="read", count=10),
        BadgeRule("month_streak", streak=4),
    )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)
