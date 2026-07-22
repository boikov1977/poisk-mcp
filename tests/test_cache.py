"""Smoke tests for TTL cache and rate limiter"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cache import TTLCache, RateLimiter, search_cache


def test_cache_set_get():
    c = TTLCache(max_size=10, ttl=60)
    c.set("pfx", "value", "mykey")
    assert c.get("pfx", "mykey") == "value"


def test_cache_miss():
    c = TTLCache(max_size=10, ttl=60)
    assert c.get("pfx", "nonexistent") is None


def test_cache_eviction():
    c = TTLCache(max_size=2, ttl=60)
    c.set("pfx", 1, "a")
    c.set("pfx", 2, "b")
    c.set("pfx", 3, "c")  # вытеснит "a"
    assert c.get("pfx", "a") is None
    assert c.get("pfx", "b") == 2
    assert c.get("pfx", "c") == 3


def test_cache_expiry():
    import time
    c = TTLCache(max_size=10, ttl=0.1)
    c.set("pfx", "fresh", "x")
    assert c.get("pfx", "x") == "fresh"
    time.sleep(0.15)
    assert c.get("pfx", "x") is None


def test_cache_invalidate_prefix():
    """invalidate(prefix) корректно удаляет все записи с указанным prefix,
    используя reverse-index _prefix_map (раньше это было сломано из-за MD5-хеширования ключей)."""
    c = TTLCache(max_size=10, ttl=60)
    c.set("search", [{"title": "A"}], "k1")
    c.set("search", [{"title": "B"}], "k2")
    c.set("other", "val", "k1")
    assert c.get("search", "k1") is not None
    c.invalidate("search")
    # Все записи с prefix="search" должны быть удалены
    assert c.get("search", "k1") is None
    assert c.get("search", "k2") is None
    # Записи с другим prefix остаются нетронутыми
    assert c.get("other", "k1") == "val"


def test_cache_invalidate_all():
    c = TTLCache(max_size=10, ttl=60)
    c.set("pfx", 1, "a")
    c.set("pfx", 2, "b")
    c.invalidate(None)
    assert c.get("pfx", "a") is None
    assert c.get("pfx", "b") is None


def test_cache_stats():
    c = TTLCache(max_size=10, ttl=60)
    c.get("pfx", "miss1")
    c.get("pfx", "miss2")
    c.set("pfx", "v", "hit")
    c.get("pfx", "hit")
    stats = c.get_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2
    assert stats["size"] == 1


def test_rate_limiter_accept():
    rl = RateLimiter(rpm=60)  # 1/sec
    assert rl.acquire() is True


def test_rate_limiter_reject():
    rl = RateLimiter(rpm=1)  # очень медленно
    rl.acquire()  # съедаем единственный токен
    assert rl.acquire() is False  # должен отклонить


def test_global_cache_instance():
    assert search_cache is not None
    stats = search_cache.get_stats()
    assert "hits" in stats
    assert "misses" in stats
