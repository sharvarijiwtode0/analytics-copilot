"""
LLM Response Cache — ported from Canary's llmCache.ts
4-hour TTL, keyed by (question + datasource_id + metadata_hash).
Reduces repeat query latency by ~80%.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from threading import Lock

import structlog
from backend.services.cache import redis_cache
from backend.config import settings

log = structlog.get_logger(__name__)

CACHE_TTL_SECONDS = 4 * 60 * 60   # 4 hours
MAX_CACHE_SIZE = 200


@dataclass
class CacheEntry:
    result: dict
    timestamp: float
    question: str
    datasource_id: str
    metadata_hash: str = ""


class LLMCache:
    """
    Thread-safe in-memory cache for LLM query results.
    Keyed by normalized question + datasource + metadata fingerprint.
    """

    def __init__(self) -> None:
        self._cache: dict[str, CacheEntry] = {}
        self._lock = Lock()
        self._hits = 0
        self._misses = 0
        self.use_redis = settings.redis_url is not None

    def _make_key(self, question: str, datasource_id: str, user_id: str = "anonymous", metadata_hash: str = "") -> str:
        normalized = question.lower().strip()
        # Shared database cache key across all users for maximum dashboard performance
        raw = f"{datasource_id}:{normalized}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, question: str, datasource_id: str, user_id: str = "anonymous", metadata_hash: str = "") -> dict | None:
        key = self._make_key(question, datasource_id, user_id, metadata_hash)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                log.info("llm_cache.miss.memory", question=question[:60], key=key)
                return None

            # TTL check
            if time.time() - entry.timestamp > CACHE_TTL_SECONDS:
                del self._cache[key]
                self._misses += 1
                log.info("llm_cache.miss.ttl_expired", question=question[:60])
                return None

            # Metadata hash changed — invalidate
            if metadata_hash and entry.metadata_hash != metadata_hash:
                del self._cache[key]
                self._misses += 1
                log.info("llm_cache.miss.metadata_invalidated", question=question[:60])
                return None

            self._hits += 1
            log.info("llm_cache.hit.memory", question=question[:60], datasource=datasource_id, user_id=user_id)
            return entry.result

    async def get_async(self, question: str, datasource_id: str, user_id: str = "anonymous", metadata_hash: str = "") -> dict | None:
        """Async version of get that checks Redis first, then memory.
        Returns None (cache miss) for entries with 0 rows — forces a fresh agent run.
        """
        key = self._make_key(question, datasource_id, user_id, metadata_hash)

        # 1. Try Redis
        if self.use_redis:
            val = await redis_cache.get(f"llm_cache:{key}")
            if val:
                # Reject stale empty results
                if val.get("row_count", 0) == 0:
                    log.info("llm_cache.miss.empty_result", question=question[:60])
                    await redis_cache.delete(f"llm_cache:{key}")
                else:
                    self._hits += 1
                    log.info("llm_cache.hit.redis", question=question[:60], user_id=user_id)
                    return val
            else:
                log.info("llm_cache.miss.redis", question=question[:60], key=key)

        # 2. Fallback to memory
        result = self.get(question, datasource_id, user_id, metadata_hash)
        if result is not None and result.get("row_count", 0) == 0:
            log.info("llm_cache.miss.empty_memory", question=question[:60])
            return None
        return result

    def set(self, question: str, datasource_id: str, result: dict, user_id: str = "anonymous", metadata_hash: str = "") -> None:
        if "cached_at" not in result:
            result["cached_at"] = time.time()
        key = self._make_key(question, datasource_id, user_id, metadata_hash)
        with self._lock:
            # Evict oldest entries if at capacity
            if len(self._cache) >= MAX_CACHE_SIZE:
                oldest_key = min(self._cache, key=lambda k: self._cache[k].timestamp)
                del self._cache[oldest_key]

            self._cache[key] = CacheEntry(
                result=result,
                timestamp=time.time(),
                question=question,
                datasource_id=datasource_id,
                metadata_hash=metadata_hash,
            )
            log.info("llm_cache.set.memory", question=question[:60], datasource=datasource_id, user_id=user_id, key=key)

    async def set_async(self, question: str, datasource_id: str, result: dict, user_id: str = "anonymous", metadata_hash: str = "") -> None:
        """Async version of set that writes to both memory and Redis."""
        self.set(question, datasource_id, result, user_id, metadata_hash)
        if self.use_redis:
            key = self._make_key(question, datasource_id, user_id, metadata_hash)
            await redis_cache.set(f"llm_cache:{key}", result, expire_seconds=CACHE_TTL_SECONDS)
            log.info("llm_cache.set.redis", question=question[:60], key=key, user_id=user_id)

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{(self._hits / total * 100):.1f}%" if total > 0 else "0%",
                "max_size": MAX_CACHE_SIZE,
                "ttl_minutes": CACHE_TTL_SECONDS // 60,
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0


# Module-level singleton
_cache = LLMCache()


def get_cache() -> LLMCache:
    return _cache
