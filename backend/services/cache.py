import json
import time
import structlog
from redis import asyncio as aioredis
from typing import Any, Optional
from backend.config import settings

log = structlog.get_logger(__name__)

class RedisCache:
    def __init__(self):
        self.redis = None
        self._last_attempt = 0.0
        self._cooldown = 10.0  # seconds

    async def connect(self):
        if self.redis:
            return

        now = time.time()
        if now - self._last_attempt < self._cooldown:
            return

        self._last_attempt = now
        try:
            # Set explicit connect and socket timeouts (0.5s) to prevent request blocking
            client = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=0.5,
                socket_connect_timeout=0.5,
                retry_on_timeout=False,
            )
            await client.ping()
            self.redis = client
            log.info("redis.connected", url=settings.redis_url)
        except Exception as e:
            log.error("redis.connection_failed_cooldown_active", error=str(e), cooldown=self._cooldown)
            self.redis = None

    async def get(self, key: str) -> Optional[dict[str, Any]]:
        await self.connect()
        if not self.redis:
            return None
        try:
            val = await self.redis.get(key)
            if val:
                return json.loads(val)
        except Exception as e:
            log.warning("redis.get_failed", key=key, error=str(e))
            # If a connection error occurs, clear self.redis to trigger reconnect after cooldown
            self.redis = None
        return None

    async def set(self, key: str, value: dict[str, Any], expire_seconds: int = 3600):
        await self.connect()
        if not self.redis:
            return
        try:
            await self.redis.setex(key, expire_seconds, json.dumps(value))
        except Exception as e:
            log.warning("redis.set_failed", key=key, error=str(e))
            self.redis = None

    async def delete(self, key: str) -> None:
        await self.connect()
        if not self.redis:
            return
        try:
            await self.redis.delete(key)
        except Exception as e:
            log.warning("redis.delete_failed", key=key, error=str(e))
            self.redis = None

redis_cache = RedisCache()

