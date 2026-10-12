"""Built-in strategies. Each is configuration for a connector: pass the source-specific bits (URLs,
parsers) to the constructor. Register an instance under its method name in ``Connector.strategies()``."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from tsp.crawl import Page, normalise, parse_sitemap

from .connector import ConnectorContext, Item, Strategy, StrategyFailed


@dataclass
class Entry:
    external_id: str
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None


@dataclass
class Listing:
    entries: list[Entry]
    next_url: str | None = None


class HttpListStrategy:
    """Paginated HTML listing: fetch a list page, parse its entries and next link, then fetch each
    entry's page as Markdown. ``start_url(stream, since)`` and ``parse(page)`` hold the site-specific
    logic."""

    def __init__(
        self,
        start_url: Callable[[str, str | None], str],
        parse: Callable[[Page], Listing],
        *,
        fetch_detail: bool = True,
        max_pages: int = 1000,
    ):
        self._start, self._parse = start_url, parse
        self._detail, self._max_pages = fetch_detail, max_pages

    async def available(self, ctx: ConnectorContext) -> bool:
        return ctx.source.robots_ok

    async def fetch(
        self, ctx: ConnectorContext, stream: str, since: str | None
    ) -> AsyncIterator[Item]:
        url: str | None = self._start(stream, since)
        seen: set[str] = set()
        while url and url not in seen and len(seen) < self._max_pages:
            seen.add(url)
            listing = self._parse(await ctx.get_page(url))
            for e in listing.entries:
                content: bytes | None = None
                meta = dict(e.metadata)
                if self._detail and e.url:
                    detail = await ctx.get_page(e.url)
                    content = detail.markdown.encode()
                    meta.setdefault("title", detail.title)
                yield Item(
                    external_id=e.external_id,
                    url=e.url,
                    content=content,
                    content_type="text/markdown" if content is not None else "application/json",
                    metadata=meta,
                    updated_at=e.updated_at,
                )
            url = listing.next_url


class SitemapStrategy:
    """Every URL in the source's sitemap (index files followed two levels deep), as Markdown."""

    def __init__(
        self,
        sitemap_url: str | None = None,
        *,
        include: Callable[[str], bool] | None = None,
        max_urls: int = 10_000,
    ):
        self._url, self._include, self._max = sitemap_url, include, max_urls

    async def available(self, ctx: ConnectorContext) -> bool:
        return ctx.source.robots_ok

    async def fetch(
        self, ctx: ConnectorContext, stream: str, since: str | None
    ) -> AsyncIterator[Item]:
        root = self._url or ctx.source.url.rstrip("/") + "/sitemap.xml"
        queue: list[tuple[str, int]] = [(root, 0)]
        seen, emitted = {root}, 0
        while queue:
            url, depth = queue.pop(0)
            raw = await ctx.get_raw(url)
            try:
                entries = parse_sitemap(raw.body)
            except Exception as e:
                raise StrategyFailed(f"bad sitemap {url}: {e}") from e
            for entry in entries:
                if entry.is_sitemap:
                    if depth < 2 and entry.loc not in seen:
                        seen.add(entry.loc)
                        queue.append((entry.loc, depth + 1))
                    continue
                if (self._include and not self._include(entry.loc)) or (
                    since and entry.lastmod and entry.lastmod <= since
                ):
                    continue
                if emitted >= self._max:
                    return
                emitted += 1
                page = await ctx.get_page(entry.loc)
                yield Item(
                    external_id=normalise(entry.loc),
                    url=page.final_url,
                    content=page.markdown.encode(),
                    content_type="text/markdown",
                    metadata={"title": page.title, "lastmod": entry.lastmod},
                    updated_at=_parse_date(entry.lastmod),
                )


class BulkFileStrategy:
    """Download the files named in a manifest: JSON ``[{"id", "url", "updated_at"?, "content_type"?}]``
    (or ``{"files": [...]}``). For S3, publish the manifest and files through presigned URLs.
    Files larger than the ``HttpFetcher`` ``max_bytes`` fail the strategy."""

    def __init__(self, manifest_url: str | Callable[[ConnectorContext, str], str]):
        self._manifest = manifest_url

    async def available(self, ctx: ConnectorContext) -> bool:
        return True

    async def fetch(
        self, ctx: ConnectorContext, stream: str, since: str | None
    ) -> AsyncIterator[Item]:
        url = self._manifest(ctx, stream) if callable(self._manifest) else self._manifest
        raw = await ctx.get_raw(url)
        try:
            doc = json.loads(raw.body)
            files = doc["files"] if isinstance(doc, dict) else doc
            rows = [(str(f["id"]), str(f["url"]), f) for f in files]
        except (ValueError, KeyError, TypeError) as e:
            raise StrategyFailed(f"bad bulk manifest {url}: {e}") from e
        for ext_id, file_url, f in rows:
            updated = f.get("updated_at")
            if since and updated and str(updated) <= since:
                continue
            blob = await ctx.get_raw(file_url)
            yield Item(
                external_id=ext_id,
                url=file_url,
                content=blob.body,
                content_type=f.get("content_type")
                or blob.content_type
                or "application/octet-stream",
                metadata={k: v for k, v in f.items() if k not in ("id", "url")},
                updated_at=_parse_date(str(updated)) if updated else None,
            )


@dataclass
class ApiPage:
    items: list[Item]
    next_token: str | None = None


class JsonApiStrategy:
    """Paged JSON API. ``url_for(stream, since, page_token)`` builds each request URL and
    ``parse(json)`` returns the items and the next page token. The key is read from the connector
    config (``auth_key``) and sent as ``auth_header``; without it the strategy is unavailable."""

    def __init__(
        self,
        url_for: Callable[[str, str | None, str | None], str],
        parse: Callable[[Any], ApiPage],
        *,
        auth_key: str | None = None,
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer ",
        max_pages: int = 10_000,
    ):
        self._url_for, self._parse = url_for, parse
        self._auth_key, self._header, self._prefix = auth_key, auth_header, auth_prefix
        self._max_pages = max_pages

    async def available(self, ctx: ConnectorContext) -> bool:
        return self._auth_key is None or bool(ctx.config.get(self._auth_key))

    async def fetch(
        self, ctx: ConnectorContext, stream: str, since: str | None
    ) -> AsyncIterator[Item]:
        headers = (
            {self._header: self._prefix + ctx.config[self._auth_key]} if self._auth_key else {}
        )
        token: str | None = None
        for _ in range(self._max_pages):
            raw = await ctx.get_raw(self._url_for(stream, since, token), headers=headers)
            try:
                page = self._parse(json.loads(raw.body))
            except (ValueError, KeyError, TypeError) as e:
                raise StrategyFailed(f"unexpected API response: {e}") from e
            for item in page.items:
                yield item
            token = page.next_token
            if not token:
                return


class PaidApiStrategy:
    """Wraps any strategy and bills ``cost_per_item`` for each item. Stops with ``StrategyFailed`` the
    moment the next item would pass the source's monthly cap; unavailable when the cap is spent or unset."""

    def __init__(self, inner: Strategy, cost_per_item: Decimal | float | str):
        self._inner = inner
        self.cost = Decimal(str(cost_per_item))

    async def available(self, ctx: ConnectorContext) -> bool:
        return ctx.budget.can_spend(self.cost) and await self._inner.available(ctx)

    async def fetch(
        self, ctx: ConnectorContext, stream: str, since: str | None
    ) -> AsyncIterator[Item]:
        it = self._inner.fetch(ctx, stream, since).__aiter__()
        while True:
            if not ctx.budget.can_spend(self.cost):
                raise StrategyFailed("monthly paid cap reached")
            try:
                item = await it.__anext__()
            except StopAsyncIteration:
                return
            ctx.budget.charge(self.cost)
            yield item


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
