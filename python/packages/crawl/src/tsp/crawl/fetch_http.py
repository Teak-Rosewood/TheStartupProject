from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
import trafilatura

from .errors import FetchError, TooLarge, TooManyRedirects, UnsupportedContentType
from .page import NotModified, Page, content_hash
from .safe_url import Resolver, safe_url
from .urls import normalise

DEFAULT_USER_AGENT = "TSPBot/1.0 (+https://example.com/bot)"
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_TEXT_TYPES = ("text/plain", "text/markdown")
_REDIRECTS = (301, 302, 303, 307, 308)


@dataclass
class RawResponse:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    headers: httpx.Headers

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")

    @property
    def retry_after(self) -> float | None:
        try:
            return float(self.headers.get("retry-after", ""))
        except ValueError:
            return None  # HTTP-date form: callers fall back to their default backoff


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []
        self.canonical: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.hrefs.append(a["href"] or "")
        elif (
            tag == "link" and "canonical" in (a.get("rel") or "").lower().split() and a.get("href")
        ):
            self.canonical = a["href"]


def extract_links(html: str, base_url: str) -> tuple[list[str], str | None]:
    """Absolute, normalised, de-duplicated http(s) links plus the rel=canonical URL, if any."""
    parser = _Links()
    parser.feed(html)
    seen: dict[str, None] = {}
    for href in parser.hrefs:
        absolute = urljoin(base_url, href.strip())
        if urlsplit(absolute).scheme in ("http", "https"):
            seen[normalise(absolute)] = None
    canonical = urljoin(base_url, parser.canonical) if parser.canonical else None
    return list(seen), canonical


class HttpFetcher:
    """Plain HTTP fetch with SSRF protection and Trafilatura extraction.

    Redirects are followed by hand so every hop goes through ``safe_url``. The connection goes to
    the pinned IP; Host and TLS SNI/verification still use the original hostname."""

    name = "http"

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        max_bytes: int = 10_000_000,
        timeout: float = 20,
        max_redirects: int = 5,
        user_agent: str = DEFAULT_USER_AGENT,
        resolver: Resolver | None = None,
        allowed_ports: tuple[int, ...] = (80, 443),
    ):
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.user_agent = user_agent
        self._resolver = resolver
        self._ports = allowed_ports

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_raw(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> RawResponse:
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        current = url
        for _ in range(self.max_redirects + 1):
            target = await safe_url(current, resolver=self._resolver, allowed_ports=self._ports)
            parts = urlsplit(target.url)
            ip = f"[{target.ip}]" if ":" in target.ip else target.ip
            default_port = 443 if target.scheme == "https" else 80
            connect = urlunsplit(
                (target.scheme, f"{ip}:{target.port}", parts.path or "/", parts.query, "")
            )
            host_header = (
                target.host if target.port == default_port else f"{target.host}:{target.port}"
            )
            async with self._client.stream(
                "GET",
                connect,
                headers={**headers, "Host": host_header},
                extensions={"sni_hostname": target.host},
            ) as resp:
                if resp.status_code in _REDIRECTS and resp.headers.get("location"):
                    current = urljoin(target.url, resp.headers["location"])
                    continue
                body = bytearray()
                if resp.status_code != 304:
                    async for chunk in resp.aiter_bytes():
                        body += chunk
                        if len(body) > self.max_bytes:
                            raise TooLarge(
                                f"response over {self.max_bytes} bytes", status=resp.status_code
                            )
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                return RawResponse(
                    url, target.url, resp.status_code, ctype, bytes(body), resp.headers
                )
        raise TooManyRedirects(f"more than {self.max_redirects} redirects")

    async def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> Page | NotModified:
        raw = await self.fetch_raw(url, etag=etag, last_modified=last_modified)
        if raw.status == 304:
            return NotModified(url, raw.etag or etag, raw.last_modified or last_modified)
        if raw.status >= 400:
            raise FetchError(f"HTTP {raw.status} for {url}", status=raw.status)
        return await self.to_page(raw)

    async def to_page(self, raw: RawResponse) -> Page:
        if raw.content_type in _HTML_TYPES or (
            raw.content_type == "" and raw.body[:15].lower().startswith(b"<!doctype html")
        ):
            text = raw.body.decode(_charset(raw.headers) or "utf-8", errors="replace")
            markdown, title = await asyncio.to_thread(_extract, text, raw.final_url)
            links, canonical = extract_links(text, raw.final_url)
        elif raw.content_type in _TEXT_TYPES:
            markdown, title, links, canonical = (
                raw.body.decode("utf-8", errors="replace"),
                None,
                [],
                None,
            )
        else:
            raise UnsupportedContentType(raw.content_type, raw)
        return Page(
            url=raw.url,
            final_url=raw.final_url,
            status=raw.status,
            title=title,
            markdown=markdown,
            links=links,
            content_hash=content_hash(markdown),
            etag=raw.etag,
            last_modified=raw.last_modified,
            fetched_at=datetime.now(timezone.utc),
            content_type=raw.content_type or "text/html",
            fetcher=self.name,
            canonical_url=canonical,
        )


def _charset(headers: httpx.Headers) -> str | None:
    for part in headers.get("content-type", "").split(";")[1:]:
        k, _, v = part.strip().partition("=")
        if k.lower() == "charset":
            return v.strip("\"'") or None
    return None


def _extract(html: str, url: str) -> tuple[str, str | None]:
    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_tables=True,
        favor_recall=True,
    )
    meta = trafilatura.extract_metadata(html, default_url=url)
    return markdown or "", (meta.title if meta else None)
