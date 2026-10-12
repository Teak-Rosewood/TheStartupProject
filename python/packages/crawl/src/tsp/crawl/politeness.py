from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable

from tsp.cache import Cache

from .errors import RateLimited


class DomainLimiter:
    """Per-host politeness shared by every worker through ``tsp.cache``.

    Rate: a fixed-window counter (``limit`` fetches per ``window`` seconds, derived from the rps or
    the robots Crawl-delay). Backoff: after 429/503 the host is blocked until Retry-After, or an
    exponential default that doubles on repeats (max 1h)."""

    def __init__(
        self,
        cache: Cache,
        default_rps: float = 1.0,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._cache = cache
        self._rps = default_rps
        self._clock = clock
        self._sleep = sleep

    async def acquire(self, host: str, *, delay: float | None = None, max_wait: float = 60) -> None:
        """Wait for a slot on ``host``. ``delay`` is the robots Crawl-delay in seconds, if any.
        Raises ``RateLimited`` if the host is backed off for longer than ``max_wait``."""
        interval = max(delay or 0.0, 1.0 / self._rps)
        limit, window = (1, math.ceil(interval)) if interval >= 1 else (int(self._rps), 1)
        waited = 0.0
        while True:
            blocked_until = await self._cache.get_json(f"crawl:backoff:{host}")
            wait = blocked_until - self._clock() if blocked_until else 0.0
            if wait <= 0:
                now = self._clock()
                slot = f"crawl:rl:{host}:{int(now // window)}"
                if await self._cache.incr(slot, ttl=window + 1) <= limit:
                    return
                wait = window - (now % window)
            if waited + wait > max_wait:
                raise RateLimited(host, wait)
            await self._sleep(wait)
            waited += wait

    async def report(self, host: str, status: int, retry_after: float | None = None) -> None:
        """Feed back a response: 429/503 start or extend a backoff, anything else clears it."""
        if status in (429, 503):
            strikes = await self._cache.incr(f"crawl:strikes:{host}", ttl=3600)
            seconds = retry_after if retry_after is not None else min(2**strikes, 3600)
            seconds = min(seconds, 3600)
            await self._cache.set_json(
                f"crawl:backoff:{host}", self._clock() + seconds, ttl=math.ceil(seconds) + 1
            )
        elif status < 400:
            await self._cache.delete(f"crawl:strikes:{host}", f"crawl:backoff:{host}")
