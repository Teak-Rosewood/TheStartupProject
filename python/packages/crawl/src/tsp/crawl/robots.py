from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from protego import Protego
from tsp.cache import Cache

# (status, body) for the robots.txt URL. Raise on network failure. Must go through safe_url.
FetchText = Callable[[str], Awaitable[tuple[int, str]]]

_OK_TTL = 3600
_FAIL_TTL = 300


class RobotsCache:
    """robots.txt per RFC 9309: 4xx means no rules (allow all), 5xx or unreachable means disallow
    all, and that failure is cached briefly so a broken host is not hammered."""

    def __init__(self, cache: Cache, fetch_text: FetchText, *, ttl: int = _OK_TTL):
        self._cache = cache
        self._fetch = fetch_text
        self._ttl = ttl

    @staticmethod
    def _origin(url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    async def _rules(self, url: str) -> tuple[Protego, bool]:
        """Return (parsed rules, disallow_all)."""
        origin = self._origin(url)
        key = f"crawl:robots:{origin}"
        entry = await self._cache.get_json(key)
        if entry is None:
            try:
                status, body = await self._fetch(f"{origin}/robots.txt")
            except Exception:
                status, body = 599, ""
            if status >= 500:
                entry, ttl = {"deny": True, "body": ""}, _FAIL_TTL
            elif status >= 400:
                entry, ttl = {"deny": False, "body": ""}, self._ttl
            else:
                entry, ttl = {"deny": False, "body": body}, self._ttl
            await self._cache.set_json(key, entry, ttl=ttl)
        return Protego.parse(entry["body"]), bool(entry["deny"])

    async def allowed(self, url: str, user_agent: str) -> bool:
        rules, deny = await self._rules(url)
        return False if deny else bool(rules.can_fetch(url, user_agent))

    async def crawl_delay(self, url: str, user_agent: str) -> float | None:
        rules, deny = await self._rules(url)
        if deny:
            return None
        delay = rules.crawl_delay(user_agent)
        return float(delay) if delay else None

    async def sitemaps(self, url: str) -> list[str]:
        rules, deny = await self._rules(url)
        return [] if deny else list(rules.sitemaps)
