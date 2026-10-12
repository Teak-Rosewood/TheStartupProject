"""Safe, polite web fetching. See docs/plans/08b-crawl.md.

``safe_url`` is async because it resolves DNS; every fetcher calls it for the first URL and each
redirect hop, and connects to the pinned IP it returns.
"""

from .chain import ChainFetcher, needs_render
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
from .page import Fetcher, NotModified, Page
from .politeness import DomainLimiter
from .robots import RobotsCache
from .safe_url import ResolvedURL, safe_url
from .sitemap import SitemapEntry, parse_sitemap
from .urls import canonical, normalise

__all__ = [
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
