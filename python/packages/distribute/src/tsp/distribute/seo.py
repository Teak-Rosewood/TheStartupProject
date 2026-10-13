"""Structured data, sitemaps and robots.txt."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape, quoteattr

from .items import Item


def _iso(d: datetime) -> str:
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).isoformat()


def jsonld_article(
    item: Item,
    lang: str,
    base_url: str,
    *,
    publisher: str,
    publisher_logo: str | None = None,
    default_lang: str = "en",
) -> dict[str, Any]:
    """schema.org ``NewsArticle`` for one language version. For gated items it carries
    ``isAccessibleForFree: false`` and the CSS selector of the gated part, so search engines read the
    paywall as a paywall and not as cloaking."""
    code, v = item.variant(lang, default_lang)
    url = item.url(base_url, code)
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": v.title[:110],
        "description": v.summary,
        "inLanguage": code,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "datePublished": _iso(item.published_at),
        "dateModified": _iso(item.updated_at or item.published_at),
        "author": [{"@type": "Person", "name": a} for a in item.authors]
        or [{"@type": "Organization", "name": publisher}],
        "publisher": {"@type": "Organization", "name": publisher},
        "isAccessibleForFree": item.free,
    }
    if publisher_logo:
        data["publisher"]["logo"] = {"@type": "ImageObject", "url": publisher_logo}
    if item.image_url:
        data["image"] = [item.image_url]
    if item.keywords:
        data["keywords"] = ", ".join(item.keywords)
    if not item.free:
        data["hasPart"] = {
            "@type": "WebPageElement",
            "isAccessibleForFree": False,
            "cssSelector": item.paywall_selector,
        }
    return data


def _xml(body: str, extra_ns: str = "") -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}'


def sitemap(
    items: Iterable[Item], base_url: str, languages: Sequence[str], lang: str | None = None
) -> str:
    """A sitemap. With ``lang`` only that language's URLs are listed; every entry still points at its
    alternates (``xhtml:link``), which is how search engines connect translations."""
    rows = []
    for item in sorted(items, key=lambda i: i.id):
        have = [lg for lg in languages if lg in item.variants]
        for code in have if lang is None else [lg for lg in have if lg == lang]:
            alts = "".join(
                f'<xhtml:link rel="alternate" hreflang={quoteattr(a)} href={quoteattr(item.url(base_url, a))}/>'
                for a in have
            )
            rows.append(
                f"<url><loc>{escape(item.url(base_url, code))}</loc>"
                f"<lastmod>{_iso(item.updated_at or item.published_at)}</lastmod>{alts}</url>"
            )
    return _xml(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">' + "".join(rows) + "</urlset>"
    )


def news_sitemap(
    items: Iterable[Item],
    base_url: str,
    languages: Sequence[str],
    publication: str,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(days=2),
) -> str:
    """Google News sitemap: only items published in the last two days, one entry per language."""
    now = now or datetime.now(UTC)
    rows = []
    for item in sorted(items, key=lambda i: i.published_at, reverse=True):
        pub = (
            item.published_at if item.published_at.tzinfo else item.published_at.replace(tzinfo=UTC)
        )
        if now - pub > max_age or pub > now:
            continue
        for code in (lg for lg in languages if lg in item.variants):
            rows.append(
                f"<url><loc>{escape(item.url(base_url, code))}</loc><news:news><news:publication>"
                f"<news:name>{escape(publication)}</news:name><news:language>{escape(code)}</news:language>"
                f"</news:publication><news:publication_date>{_iso(pub)}</news:publication_date>"
                f"<news:title>{escape(item.variants[code].title)}</news:title></news:news></url>"
            )
    return _xml(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">' + "".join(rows) + "</urlset>"
    )


def sitemap_index(base_url: str, languages: Sequence[str], *, news: bool = True) -> str:
    base = base_url.rstrip("/")
    locs = [f"{base}/sitemap-{lg}.xml" for lg in languages] + (
        [f"{base}/sitemap-news.xml"] if news else []
    )
    return _xml(
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(f"<sitemap><loc>{escape(u)}</loc></sitemap>" for u in locs)
        + "</sitemapindex>"
    )


def robots_txt(
    base_url: str,
    *,
    disallow: Sequence[str] = ("/api/", "/app/"),
    allow_ai_crawlers: bool = True,
    ai_crawlers: Sequence[str] = (
        "GPTBot",
        "ClaudeBot",
        "CCBot",
        "Google-Extended",
        "PerplexityBot",
    ),
    sitemaps: Sequence[str] | None = None,
) -> str:
    """robots.txt. Set ``allow_ai_crawlers=False`` to ask AI training crawlers to stay out. robots.txt
    is a request, not enforcement."""
    lines = ["User-agent: *", *[f"Disallow: {p}" for p in disallow], "Allow: /", ""]
    if not allow_ai_crawlers:
        for bot in ai_crawlers:
            lines += [f"User-agent: {bot}", "Disallow: /", ""]
    for s in sitemaps if sitemaps is not None else [f"{base_url.rstrip('/')}/sitemap.xml"]:
        urlparse(s)  # fail early on garbage
        lines.append(f"Sitemap: {s}")
    return "\n".join(lines) + "\n"
