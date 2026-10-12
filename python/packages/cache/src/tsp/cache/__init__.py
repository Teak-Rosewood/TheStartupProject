"""Valkey/Redis cache: JSON get/set, ``@cached``, sliding-window rate limiter, distributed lock.

``Cache.from_url(None)`` (or ``"none"``) returns a no-op cache so an app still runs without one.
"""

from .client import Cache, LockNotAcquired, NullCache, cache_from_url

__all__ = ["Cache", "LockNotAcquired", "NullCache", "cache_from_url"]
