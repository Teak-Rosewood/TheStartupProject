"""Verify search-engine crawlers the way Google and Bing document: reverse DNS, then forward-confirm."""

from __future__ import annotations

import asyncio
import socket
from typing import Protocol

# user-agent marker -> hostname suffixes the reverse lookup must end with
CRAWLERS = {
    "googlebot": (".googlebot.com", ".google.com"),
    "bingbot": (".search.msn.com",),
}


class Resolver(Protocol):
    async def reverse(self, ip: str) -> str | None: ...

    async def forward(self, host: str) -> list[str]: ...


class SystemResolver:
    async def reverse(self, ip: str) -> str | None:
        try:
            return (await asyncio.to_thread(socket.gethostbyaddr, ip))[0].lower()
        except OSError:
            return None

    async def forward(self, host: str) -> list[str]:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except OSError:
            return []
        return sorted({str(i[4][0]) for i in infos})


async def verify_crawler(ip: str | None, user_agent: str, resolver: Resolver) -> str | None:
    """The crawler's name if this request really comes from it, else None. A user agent alone proves nothing."""
    ua = user_agent.lower()
    name = next((n for n in CRAWLERS if n in ua), None)
    if name is None or not ip:
        return None
    host = await resolver.reverse(ip)
    if not host or not host.endswith(CRAWLERS[name]):
        return None
    return name if ip in await resolver.forward(host) else None


def paywall_jsonld(headline: str, selector: str = ".paywalled") -> dict[str, object]:
    """schema.org markup that tells search engines the gated part is a paywall, not cloaking."""
    return {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": headline,
        "isAccessibleForFree": False,
        "hasPart": {
            "@type": "WebPageElement",
            "isAccessibleForFree": False,
            "cssSelector": selector,
        },
    }
