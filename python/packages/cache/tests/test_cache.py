import os

import pytest
from tsp.cache import LockNotAcquired, NullCache, cache_from_url  # noqa: F401

URL = os.environ.get("TSP_TEST_CACHE_URL", "redis://127.0.0.1:56379/0")


@pytest.fixture
async def cache():
    c = cache_from_url(URL, prefix=f"t{os.getpid()}:")
    try:
        await c.ping()
    except Exception:
        pytest.skip("no Valkey/Redis at TSP_TEST_CACHE_URL")
    yield c
    await c.close()


async def test_json_roundtrip_and_delete(cache):
    await cache.set_json("a", {"x": [1, 2]}, ttl=10)
    assert await cache.get_json("a") == {"x": [1, 2]}
    await cache.delete("a")
    assert await cache.get_json("a") is None


async def test_rate_limit_allows_then_blocks(cache):
    results = [await cache.allow("u1", 3, 5) for _ in range(5)]
    assert results == [True, True, True, False, False]


async def test_cached_decorator_calls_once(cache):
    calls = []

    @cache.cached(ttl=10)
    async def work(n: int) -> int:
        calls.append(n)
        return n * 2

    assert [await work(4), await work(4)] == [8, 8]
    assert calls == [4]


async def test_lock_is_exclusive(cache):
    async with cache.lock("job", ttl=5):
        with pytest.raises(LockNotAcquired):
            async with cache.lock("job", ttl=5):
                pass
    async with cache.lock("job", ttl=5):  # released
        pass


async def test_incr_sets_ttl_once(cache):
    assert await cache.incr("c", ttl=5) == 1
    assert await cache.incr("c", ttl=5) == 2


async def test_null_cache_is_a_safe_noop():
    c = cache_from_url(None)
    assert isinstance(c, NullCache)
    await c.set_json("k", 1)
    assert await c.get_json("k") is None
    assert await c.allow("k", 1, 1)
    async with c.lock("x"):
        pass
