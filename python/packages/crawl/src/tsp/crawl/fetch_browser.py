from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .errors import FetchError, RenderUnavailable
from .page import NotModified, Page, content_hash
from .safe_url import Resolver, safe_url
from .urls import normalise


@dataclass
class Rendered:
    final_url: str
    status: int
    title: str | None
    markdown: str
    links: list[str] = field(default_factory=list)


Renderer = Callable[[str], Awaitable[Rendered]]


async def _crawl4ai_render(url: str) -> Rendered:
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    except ImportError as e:
        raise RenderUnavailable("install tsp-crawl[browser] to render JavaScript pages") from e
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(url=url, config=CrawlerRunConfig())
    if not result.success:
        raise FetchError(f"render failed: {result.error_message}", status=result.status_code)
    md = result.markdown
    links = [
        normalise(link["href"])
        for link in (result.links or {}).get("internal", [])
        if link.get("href", "").startswith(("http://", "https://"))
    ]
    return Rendered(
        final_url=result.redirected_url or url,
        status=result.status_code or 200,
        title=(result.metadata or {}).get("title"),
        markdown=str(md),
        links=list(dict.fromkeys(links)),
    )


class BrowserFetcher:
    """Renders JavaScript pages with Crawl4AI (extra ``browser``). Run it only in the isolated render
    worker: no inbound, no database secrets (plan 8b-C).

    The start URL and the final URL are checked with ``safe_url``. Subresource requests made inside
    the browser are not filtered here, so the render worker's network egress must block private
    ranges. Conditional GET is not supported; ``etag`` and ``last_modified`` are ignored."""

    name = "browser"

    def __init__(
        self,
        renderer: Renderer | None = None,
        *,
        resolver: Resolver | None = None,
        allowed_ports: tuple[int, ...] = (80, 443),
    ):
        self._render = renderer or _crawl4ai_render
        self._resolver = resolver
        self._ports = allowed_ports

    async def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Page | NotModified:
        await safe_url(url, resolver=self._resolver, allowed_ports=self._ports)
        r = await self._render(url)
        await safe_url(r.final_url, resolver=self._resolver, allowed_ports=self._ports)
        if r.status >= 400:
            raise FetchError(f"HTTP {r.status} for {url}", status=r.status)
        return Page(
            url=url,
            final_url=r.final_url,
            status=r.status,
            title=r.title,
            markdown=r.markdown,
            links=r.links,
            content_hash=content_hash(r.markdown),
            etag=None,
            last_modified=None,
            fetched_at=datetime.now(timezone.utc),
            content_type="text/html",
            fetcher=self.name,
        )
