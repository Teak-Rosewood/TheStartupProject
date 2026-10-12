from __future__ import annotations

from dataclasses import dataclass

from .fetch_http import DEFAULT_USER_AGENT

MAX_PAGES_HARD_CAP = 10_000
MAX_PATTERNS = 20


@dataclass(frozen=True)
class CrawlSettings:
    """Per-deployment knobs. Per-plan limits (``crawl_pages_month``, ``crawl_depth``,
    ``crawl_refresh_min_hours``) come from the ``limits`` callable instead."""

    user_agent: str = DEFAULT_USER_AGENT
    rps: float = 1.0  # per host, shared by every worker through tsp.cache
    max_wait_seconds: float = 60  # longest a fetch waits on the per-host limiter
