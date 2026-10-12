from __future__ import annotations

from typing import Any


class CrawlError(Exception):
    """Base class for everything this package raises on purpose."""


class UnsafeURL(CrawlError):
    """The URL is not allowed: bad scheme or port, credentials, or it resolves to a non-public address."""


class FetchError(CrawlError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class TooLarge(FetchError):
    """The response body exceeded ``max_bytes``."""


class TooManyRedirects(FetchError):
    pass


class UnsupportedContentType(FetchError):
    """The fetcher cannot turn this content type into Markdown. ``raw`` holds the downloaded response."""

    def __init__(self, content_type: str, raw: Any = None):
        super().__init__(f"unsupported content type: {content_type}")
        self.content_type = content_type
        self.raw = raw


class RateLimited(CrawlError):
    """The host is in backoff for longer than the caller is willing to wait."""

    def __init__(self, host: str, retry_after: float):
        super().__init__(f"{host} rate limited; retry in {retry_after:.0f}s")
        self.host = host
        self.retry_after = retry_after


class RenderUnavailable(CrawlError):
    """An optional extra (browser, pdf) is not installed."""
