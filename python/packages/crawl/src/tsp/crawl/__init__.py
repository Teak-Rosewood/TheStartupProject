"""Safe, polite web fetching. See docs/plans/08b-crawl.md.

``safe_url`` is async because it resolves DNS; every fetcher calls it for the first URL and each
redirect hop, and connects to the pinned IP it returns.
"""

from pathlib import Path

from .api import build_router
from .chain import ChainFetcher, needs_render
from .compliance import register_with_compliance
from .config import CrawlSettings
from .errors import (
    CrawlError,
    FetchError,
    RateLimited,
    RenderUnavailable,
    TooLarge,
    TooManyRedirects,
    UnsafeURL,
    UnsupportedContentType,
)
from .fetch_browser import BrowserFetcher
from .fetch_http import HttpFetcher
from .fetch_pdf import PdfFetcher
from .jobs import register_jobs
from .models import CrawlPage, CrawlSource
from .page import Fetcher, NotModified, Page
from .politeness import DomainLimiter
from .robots import RobotsCache
from .safe_url import ResolvedURL, safe_url
from .service import CrawlService, in_scope
from .sitemap import SitemapEntry, parse_sitemap
from .urls import canonical, normalise

__all__ = [
    "CrawlPage",
    "CrawlService",
    "CrawlSettings",
    "CrawlSource",
    "build_router",
    "in_scope",
    "migrations_path",
    "register_jobs",
    "register_with_compliance",
    "BrowserFetcher",
    "ChainFetcher",
    "CrawlError",
    "DomainLimiter",
    "Fetcher",
    "FetchError",
    "HttpFetcher",
    "NotModified",
    "Page",
    "PdfFetcher",
    "RateLimited",
    "RenderUnavailable",
    "ResolvedURL",
    "RobotsCache",
    "SitemapEntry",
    "TooLarge",
    "TooManyRedirects",
    "UnsafeURL",
    "UnsupportedContentType",
    "canonical",
    "needs_render",
    "normalise",
    "parse_sitemap",
    "safe_url",
]


def migrations_path() -> Path:
    """Alembic version folder; pass this module to ``tsp.db.upgrade(modules=["tsp.crawl"])``."""
    return Path(__file__).parent / "migrations" / "versions"
