"""Interest signals for topics, scored daily into a candidate list for an editor."""

from pathlib import Path

from .api import build_router
from .jobs import register_jobs
from .models import CandidateRow, SignalRow, TopicRow
from .scorer import Point, Scored, combine, daily_value, dedupe, series_z, zscore
from .service import Conflict, NotFound, TrendsConfig, TrendsError, TrendsService
from .signals import (
    CustomFeedSignal,
    EmergingTermsSignal,
    FeedVolumeSignal,
    GdeltVolumeSignal,
    SearchTrendsSignal,
    Signal,
    SignalPoint,
    SignalUnavailable,
    Topic,
    TrendsContext,
    parse_feed,
    scrape_rung,
)

__all__ = [
    "CandidateRow",
    "Conflict",
    "CustomFeedSignal",
    "EmergingTermsSignal",
    "FeedVolumeSignal",
    "GdeltVolumeSignal",
    "NotFound",
    "Point",
    "Scored",
    "SearchTrendsSignal",
    "Signal",
    "SignalPoint",
    "SignalRow",
    "SignalUnavailable",
    "Topic",
    "TopicRow",
    "TrendsConfig",
    "TrendsContext",
    "TrendsError",
    "TrendsService",
    "build_router",
    "combine",
    "daily_value",
    "dedupe",
    "migrations_path",
    "parse_feed",
    "register_jobs",
    "scrape_rung",
    "series_z",
    "zscore",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.trends"])``."""
    return Path(__file__).parent / "migrations" / "versions"
