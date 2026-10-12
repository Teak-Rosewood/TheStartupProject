from __future__ import annotations

import functools
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

import redis.asyncio as redis
from redis.exceptions import LockError

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


class LockNotAcquired(Exception):
    pass


class Cache:
    """Thin wrapper over a redis-py asyncio client. TLS is chosen by the URL scheme (``rediss://``)."""

    def __init__(self, client: redis.Redis, prefix: str = ""):
        self._r = client
        self._prefix = prefix

    def _k(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def get_json(self, key: str) -> Any | None:
        raw = await self._r.get(self._k(key))
        return None if raw is None else json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self._r.set(self._k(key), json.dumps(value), ex=ttl)

    async def delete(self, *keys: str) -> None:
        if keys:
            await self._r.delete(*(self._k(k) for k in keys))

    async def incr(self, key: str, ttl: int | None = None) -> int:
        k = self._k(key)
        value = int(await self._r.incr(k))
        if ttl and value == 1:
            await self._r.expire(k, ttl)
        return value

    async def ping(self) -> bool:
        return bool(await self._r.ping())

    async def close(self) -> None:
        await self._r.aclose()

    async def allow(self, key: str, limit: int, window_s: int) -> bool:
        """Sliding-window rate limit: True if this call is within ``limit`` per ``window_s``."""
        k = self._k(f"rl:{key}")
        now = time.time()
        pipe = self._r.pipeline()
        pipe.zremrangebyscore(k, 0, now - window_s)
        pipe.zadd(k, {uuid.uuid4().hex: now})
        pipe.zcard(k)
        pipe.expire(k, window_s)
        _, _, count, _ = await pipe.execute()
        return int(count) <= limit

    @asynccontextmanager
    async def lock(self, name: str, ttl: int = 30, wait: float = 0) -> AsyncIterator[None]:
        """Distributed lock. Raises LockNotAcquired if not obtained within ``wait`` seconds."""
        lock = self._r.lock(self._k(f"lock:{name}"), timeout=ttl, blocking_timeout=wait or None)
        if not await lock.acquire(blocking=bool(wait)):
            raise LockNotAcquired(name)
        try:
            yield
        finally:
            try:
                await lock.release()
            except LockError:  # expired while held; nothing to release
                pass

    def cached(self, ttl: int, key: Callable[..., str] | None = None) -> Callable[[F], F]:
        """Cache an async function's JSON-serializable result for ``ttl`` seconds."""

        def deco(fn: F) -> F:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                k = (
                    key(*args, **kwargs)
                    if key
                    else f"fn:{fn.__qualname__}:{args!r}:{sorted(kwargs.items())!r}"
                )
                hit = await self.get_json(k)
                if hit is not None:
                    return hit
                value = await fn(*args, **kwargs)
                await self.set_json(k, value, ttl)
                return value

            return wrapper  # type: ignore[return-value]

        return deco


class NullCache(Cache):
    """Same interface, does nothing: misses on read, allows everything. For ``cache.mode: none``."""

    def __init__(self) -> None:
        pass

    async def get_json(self, key: str) -> Any | None:
        return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        return None

    async def incr(self, key: str, ttl: int | None = None) -> int:
        return 1

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    async def allow(self, key: str, limit: int, window_s: int) -> bool:
        return True

    @asynccontextmanager
    async def lock(self, name: str, ttl: int = 30, wait: float = 0) -> AsyncIterator[None]:
        yield


def cache_from_url(url: str | None, prefix: str = "") -> Cache:
    if not url or url.lower() == "none":
        return NullCache()
    return Cache(redis.from_url(url, decode_responses=True), prefix)
