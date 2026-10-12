"""Pick a language per request: URL prefix beats cookie beats Accept-Language beats the default."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from fastapi import Request

COOKIE = "lang"


@dataclass(frozen=True)
class Negotiated:
    lang: str
    source: str  # "url" | "cookie" | "header" | "default"
    path: str  # the path without the language prefix


def _base(tag: str) -> str:
    return tag.strip().lower().replace("_", "-").split("-")[0]


def _accept(header: str) -> list[str]:
    """Language tags from an Accept-Language header, best first (q-values; ``q=0`` is dropped)."""
    scored: list[tuple[float, int, str]] = []
    for i, part in enumerate(p for p in header.split(",") if p.strip()):
        tag, _, params = part.partition(";")
        m = re.search(r"q\s*=\s*([^;,\s]+)", params)
        try:
            q = float(m.group(1)) if m else 1.0
        except ValueError:
            q = 0.0
        if q > 0 and tag.strip() != "*":
            scored.append((-q, i, _base(tag)))
    return [t for _, _, t in sorted(scored)]


def negotiate(
    path: str,
    supported: Sequence[str],
    default: str,
    *,
    cookie: str | None = None,
    accept_language: str | None = None,
) -> Negotiated:
    ok = {s.lower() for s in supported}
    parts = path.lstrip("/").split("/", 1)
    if parts[0].lower() in ok:
        return Negotiated(parts[0].lower(), "url", "/" + (parts[1] if len(parts) > 1 else ""))
    plain = "/" + path.lstrip("/")
    if cookie and _base(cookie) in ok:
        return Negotiated(_base(cookie), "cookie", plain)
    for tag in _accept(accept_language or ""):
        if tag in ok:
            return Negotiated(tag, "header", plain)
    return Negotiated(default, "default", plain)


def make_get_lang(supported: Sequence[str], default: str) -> Callable[[Request], Awaitable[str]]:
    """A FastAPI dependency: ``lang: str = Depends(make_get_lang(["en", "hi"], "en"))``. Uses the URL
    prefix when the route has one, then the ``lang`` cookie, then ``Accept-Language``."""

    async def get_lang(request: Request) -> str:
        return negotiate(
            request.url.path,
            supported,
            default,
            cookie=request.cookies.get(COOKIE),
            accept_language=request.headers.get("accept-language"),
        ).lang

    return get_lang


def hreflang_links(
    base_url: str,
    path: str,
    languages: Sequence[str],
    default: str,
    *,
    available: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """``<link rel="alternate" hreflang>`` entries for one page, each language at ``/<lang><path>``.
    Pass ``available`` to list only the languages this page really exists in. Includes ``x-default``
    (the default language's URL) and always lists the page itself, as search engines require."""
    base = base_url.rstrip("/")
    path = "/" + path.lstrip("/")
    live = [lang for lang in languages if available is None or lang in available]
    links = [{"hreflang": lang, "href": f"{base}/{lang}{path}"} for lang in live]
    if default in live:
        links.append({"hreflang": "x-default", "href": f"{base}/{default}{path}"})
    return links


def missing_languages(configured: Sequence[str], available: Sequence[str]) -> list[str]:
    """Languages an item still lacks; an empty list means every variant is ready (publish check)."""
    have = set(available)
    return [lang for lang in configured if lang not in have]
