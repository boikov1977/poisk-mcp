# cache.py
import time
import threading
import hashlib
from typing import Any, Dict, List
from collections import OrderedDict
from config import config

class TTLCache:
    def __init__(self, max_size=1000, ttl=3600):
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._timestamps: Dict[str, float] = {}
        # Reverse-index: human-readable prefix → список MD5-ключей.
        # Нужен потому что _make_key() хеширует ключи и оригинальный prefix
        # теряется, поэтому invalidate(prefix) не может работать через startswith.
        self._prefix_map: Dict[str, List[str]] = {}
        # Обратный мапинг key → prefix для O(1) удаления из _prefix_map.
        self._key_prefix: Dict[str, str] = {}
        self._max_size = max_size
        self._ttl = ttl
        self._lock = threading.RLock()
        self.stats = {"hits": 0, "misses": 0}
    
    def _make_key(self, prefix, *args, **kwargs):
        sorted_items = sorted(kwargs.items(), key=lambda x: (str(x[0]), str(x[1])))
        data = f"{prefix}:{args}:{sorted_items}"
        return hashlib.md5(data.encode()).hexdigest()
    
    def get(self, prefix, *args, **kwargs):
        key = self._make_key(prefix, *args, **kwargs)
        with self._lock:
            if key not in self._cache:
                self.stats["misses"] += 1
                return None
            
            # Проверка TTL
            if time.time() - self._timestamps.get(key, 0) > self._ttl:
                self._remove_key(key)
                self.stats["misses"] += 1
                return None
            
            # O(1) операция перемещения в конец (наиболее используемый)
            self._cache.move_to_end(key)
            self.stats["hits"] += 1
            return self._cache[key]
    
    def set(self, prefix, value, *args, **kwargs):
        key = self._make_key(prefix, *args, **kwargs)
        with self._lock:
            # Если ключ уже есть, удаляем старую запись (для обновления порядка)
            if key in self._cache:
                self._remove_key(key)

            # Если кэш полон, удаляем самый старый (первый элемент)
            if len(self._cache) >= self._max_size:
                self._evict_oldest()

            self._cache[key] = value
            self._timestamps[key] = time.time()
            # Поддерживаем reverse-index: prefix → список MD5-ключей.
            # Дедуп: если ключ уже отслежен для этого prefix — не добавляем повторно.
            bucket = self._prefix_map.setdefault(prefix, [])
            if not bucket or bucket[-1] != key:
                bucket.append(key)
            self._key_prefix[key] = prefix
    
    def _remove_key(self, key):
        # O(1) удаление из словарей + поддержание reverse-index.
        self._cache.pop(key, None)
        self._timestamps.pop(key, None)
        prefix = self._key_prefix.pop(key, None)
        if prefix is not None:
            bucket = self._prefix_map.get(prefix)
            if bucket is not None:
                # O(n) по размеру bucket, но для типичных prefix-групп это мало.
                try:
                    bucket.remove(key)
                except ValueError:
                    pass
                if not bucket:
                    del self._prefix_map[prefix]

    def _evict_oldest(self):
        # O(1) удаление первого элемента (FIFO/LRU).
        if self._cache:
            oldest_key, _ = self._cache.popitem(last=False)
            self._remove_key(oldest_key)
    
    def get_stats(self):
        total = self.stats["hits"] + self.stats["misses"]
        hr = (self.stats["hits"] / total * 100) if total else 0
        return {**self.stats, "size": len(self._cache), "hit_rate_pct": round(hr, 2)}
    
    def invalidate(self, prefix=None):
        with self._lock:
            if prefix is None:
                self._cache.clear()
                self._timestamps.clear()
                self._prefix_map.clear()
                self._key_prefix.clear()
            else:
                # Используем reverse-index вместо k.startswith(prefix),
                # т.к. ключи хешируются MD5 и оригинальный prefix в них не сохраняется.
                keys_to_remove = list(self._prefix_map.get(prefix, []))
                for k in keys_to_remove:
                    self._remove_key(k)

search_cache = TTLCache()

class RateLimiter:
    def __init__(self, rpm=30):
        self._rate = rpm / 60.0
        self._capacity = rpm
        self._tokens = float(rpm)
        self._last = time.time()
        self._lock = threading.Lock()
        self._total = 0
        self._rejected = 0

    def acquire(self, timeout=None):
        """Получить токен. Если timeout задан — ждать до его истечения."""
        with self._lock:
            now = time.time()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now

            if self._tokens >= 1:
                self._tokens -= 1
                self._total += 1
                return True

            if timeout is None:
                self._rejected += 1
                return False

            # Сколько нужно ждать до появления 1 токена
            wait = (1 - self._tokens) / self._rate

        # Ждём вне основного лока, чтобы не блокировать другие потоки
        if wait > timeout:
            with self._lock:
                self._rejected += 1
            return False

        time.sleep(wait)

        with self._lock:
            now = time.time()
            self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now

            if self._tokens >= 1:
                self._tokens -= 1
                self._total += 1
                return True

            self._rejected += 1
            return False

    def get_stats(self):
        t = self._total + self._rejected
        rr = (self._rejected / t * 100) if t else 0
        return {"total": self._total, "rejected": self._rejected, "rejection_rate": round(rr, 2)}

rate_limiter = RateLimiter()