from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, Field
from tsp.crawl import (
    CrawlError,
    DomainLimiter,
    Fetcher,
    HttpFetcher,
    NotModified,
    Page,
    RateLimited,
    RobotsCache,
    UnsafeURL,
)
from tsp.crawl.fetch_http import RawResponse

from .config import SourcesConfig


class StrategyFailed(Exception):
    """This strategy cannot finish; the runner tries the next one in the source's order."""


class Item(BaseModel):
    external_id: str
    url: str | None = None
    content: bytes | None = None
    content_type: str = "application/octet-stream"
    metadata: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None

    def checksum(self) -> str:
        h = hashlib.sha256()
        h.update(self.content if self.content is not None else b"")
        h.update(self.content_type.encode())
        if self.content is None:  # metadata-only items change when their metadata does
            h.update(json.dumps(self.metadata, sort_keys=True, default=str).encode())
        return h.hexdigest()


@dataclass(frozen=True)
class SourceInfo:
    """Immutable snapshot of the ``src_sources`` row handed to strategies."""

    id: int
    key: str
    url: str
    org_id: str | None
    robots_ok: bool
    allowed_uses: tuple[str, ...]
    strategies: tuple[str, ...]
    rate_limit_rps: float
    paid_cap_monthly: Decimal | None


@dataclass
class PaidBudget:
    """Money for the current calendar month. No cap means no paid calls: paid access is opt-in."""

    cap: Decimal | None
    spent_before: Decimal = Decimal(0)
    spent_run: Decimal = Decimal(0)

    def can_spend(self, amount: Decimal) -> bool:
        return self.cap is not None and self.spent_before + self.spent_run + amount <= self.cap

    def charge(self, amount: Decimal) -> None:
        self.spent_run += amount


@dataclass
class ConnectorContext:
    """What a strategy may use. Every network call goes through ``get_raw`` or ``get_page``, which
    apply robots.txt, the per-domain limiter, ``safe_url`` and retry on 429 and 5xx."""

    source: SourceInfo
    http: HttpFetcher
    fetcher: Fetcher
    robots: RobotsCache | None
    limiter: DomainLimiter | None
    config: Mapping[str, str] = field(
        default_factory=dict
    )  # secrets and keys, for example API tokens
    budget: PaidBudget = field(default_factory=lambda: PaidBudget(None))
    options: SourcesConfig = field(default_factory=SourcesConfig)

    async def _polite(self, url: str) -> None:
        from urllib.parse import urlsplit

        host = urlsplit(url).netloc
        delay = None
        if self.robots is not None:
            ua = self.http.user_agent
            if not await self.robots.allowed(url, ua):
                raise StrategyFailed(f"robots.txt disallows {url}")
            delay = await self.robots.crawl_delay(url, ua)
        if self.limiter is not None:
            try:
                await self.limiter.acquire(
                    host, delay=delay, max_wait=self.options.max_wait_seconds
                )
            except RateLimited as e:
                raise StrategyFailed(str(e)) from e

    async def _report(self, url: str, status: int, retry_after: float | None = None) -> None:
        from urllib.parse import urlsplit

        if self.limiter is not None:
            await self.limiter.report(urlsplit(url).netloc, status, retry_after)

    async def get_raw(self, url: str, headers: dict[str, str] | None = None) -> RawResponse:
        last = "no attempt"
        for _ in range(self.options.retries + 1):
            await self._polite(url)
            try:
                raw = await self.http.fetch_raw(url, headers=headers)
            except UnsafeURL as e:
                raise StrategyFailed(f"unsafe URL {url}: {e}") from e
            except CrawlError as e:
                raise StrategyFailed(f"{url}: {e}") from e
            await self._report(url, raw.status, raw.retry_after)
            if raw.status < 400:
                return raw
            last = f"HTTP {raw.status} for {url}"
            if raw.status != 429 and raw.status < 500:
                break
        raise StrategyFailed(last)

    async def get_page(self, url: str) -> Page:
        last = "no attempt"
        for _ in range(self.options.retries + 1):
            await self._polite(url)
            try:
                page = await self.fetcher.fetch(url)
            except UnsafeURL as e:
                raise StrategyFailed(f"unsafe URL {url}: {e}") from e
            except CrawlError as e:
                status = getattr(e, "status", None)
                if status:
                    await self._report(url, status)
                last = f"{url}: {e}"
                if status is not None and status != 429 and status < 500:
                    break
                continue
            if isinstance(page, NotModified):  # we never send validators, so this is a server bug
                raise StrategyFailed(f"unexpected 304 for {url}")
            await self._report(url, page.status)
            return page
        raise StrategyFailed(last)


class Strategy(Protocol):
    """One way to get a source's data. The key it has in ``Connector.strategies()`` must be one of
    ``open_data``, ``bulk``, ``scrape``, ``api_free``, ``api_paid``."""

    async def available(self, ctx: ConnectorContext) -> bool: ...

    def fetch(
        self, ctx: ConnectorContext, stream: str, since: str | None
    ) -> AsyncIterator[Item]: ...


STRATEGY_NAMES = ("open_data", "bulk", "scrape", "api_free", "api_paid")


class Connector:
    """Subclass once per source, in the application repository, and register it."""

    key: ClassVar[str]
    streams: ClassVar[list[str]]

    def strategies(self) -> dict[str, Strategy]:
        raise NotImplementedError

    def next_cursor(self, item: Item, current: str | None) -> str | None:
        """Cursor after ``item``. Default: the newest ``updated_at`` (ISO string) seen."""
        if item.updated_at is None:
            return current
        value = item.updated_at.isoformat()
        return value if current is None or value > current else current
