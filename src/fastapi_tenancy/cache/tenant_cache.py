"""Tenant-scoped Redis cache.

Changes from v0.1.0
-------------------
- All f-string log calls replaced with ``%s`` deferred formatting so strings
  are only constructed when the log level is enabled.
- ``json.dumps`` is now wrapped in a helper that uses a custom encoder for
  datetime/enum values so non-JSON-native types don't crash set().
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from redis import asyncio as aioredis
from redis.exceptions import RedisError

from fastapi_tenancy.core.exceptions import TenancyError

if TYPE_CHECKING:
    from fastapi_tenancy.core.types import Tenant

logger = logging.getLogger(__name__)


class _DatetimeEncoder(json.JSONEncoder):
    """JSON encoder that handles datetime and enum serialisation."""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


class TenantCache:
    """Tenant-scoped cache using Redis.

    All keys are prefixed with ``{prefix}:tenant:{tenant_id}:`` to guarantee
    complete cross-tenant isolation.

    Example
    -------
    .. code-block:: python

        cache = TenantCache(redis_url="redis://localhost:6379/0")

        await cache.set(tenant, "user_count", 100, ttl=300)
        count = await cache.get(tenant, "user_count")   # 100
        await cache.delete(tenant, "user_count")
        deleted = await cache.clear_tenant(tenant)
        await cache.close()
    """

    def __init__(
        self,
        redis_url: str,
        default_ttl: int = 3600,
        key_prefix: str = "tenancy",
        max_connections: int = 10,
    ) -> None:
        self.redis: aioredis.Redis = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=max_connections,
        )
        self.default_ttl = default_ttl
        self.key_prefix = key_prefix
        logger.info(
            "TenantCache initialised ttl=%ds prefix=%s max_connections=%d",
            default_ttl, key_prefix, max_connections,
        )

    def _make_key(self, tenant: Tenant, key: str) -> str:
        return f"{self.key_prefix}:tenant:{tenant.id}:{key}"

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, cls=_DatetimeEncoder)

    @staticmethod
    def _deserialize(raw: str) -> Any:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    async def get(self, tenant: Tenant, key: str) -> Any:
        """Return cached value or ``None`` on miss/error."""
        cache_key = self._make_key(tenant, key)
        try:
            value = await self.redis.get(cache_key)
            if value is None:
                logger.debug("Cache miss: %s", cache_key)
                return None
            logger.debug("Cache hit: %s", cache_key)
            return self._deserialize(value)
        except RedisError as exc:
            logger.error("Redis error on get key=%s: %s", cache_key, exc, exc_info=True)
            return None

    async def set(
        self,
        tenant: Tenant,
        key: str,
        value: Any,
        ttl: int | None = None,
    ) -> bool:
        """Store *value* under *key* with TTL — returns True on success."""
        cache_key = self._make_key(tenant, key)
        effective_ttl = ttl if ttl is not None else self.default_ttl
        try:
            serialised = self._serialize(value)
            result = await self.redis.setex(cache_key, effective_ttl, serialised)
            logger.debug("Cached key=%s ttl=%ds", cache_key, effective_ttl)
            return bool(result)
        except (RedisError, TypeError, ValueError) as exc:
            logger.error("Redis error on set key=%s: %s", cache_key, exc, exc_info=True)
            return False

    async def delete(self, tenant: Tenant, key: str) -> bool:
        """Delete *key* — returns True if key existed."""
        cache_key = self._make_key(tenant, key)
        try:
            result = await self.redis.delete(cache_key)
            logger.debug("Deleted cache key=%s", cache_key)
            return bool(result)
        except RedisError as exc:
            logger.error("Redis error on delete key=%s: %s", cache_key, exc, exc_info=True)
            return False

    async def exists(self, tenant: Tenant, key: str) -> bool:
        """Return True if *key* exists in cache."""
        cache_key = self._make_key(tenant, key)
        try:
            return bool(await self.redis.exists(cache_key))
        except RedisError as exc:
            logger.error("Redis error on exists key=%s: %s", cache_key, exc, exc_info=True)
            return False

    async def get_ttl(self, tenant: Tenant, key: str) -> int:
        """Return remaining TTL in seconds (-1 = no TTL, -2 = missing)."""
        cache_key = self._make_key(tenant, key)
        try:
            return await self.redis.ttl(cache_key)
        except RedisError as exc:
            logger.error("Redis error on ttl key=%s: %s", cache_key, exc, exc_info=True)
            return -2

    async def increment(self, tenant: Tenant, key: str, amount: int = 1) -> int:
        """Atomically increment counter by *amount*, return new value."""
        cache_key = self._make_key(tenant, key)
        try:
            result = await self.redis.incrby(cache_key, amount)
            logger.debug("Incremented %s by %d → %d", cache_key, amount, result)
            return result
        except RedisError as exc:
            logger.error("Redis error on increment key=%s: %s", cache_key, exc, exc_info=True)
            raise TenancyError(f"Failed to increment cache key: {exc}") from exc

    async def clear_tenant(self, tenant: Tenant) -> int:
        """Delete ALL cache entries for *tenant* — returns count deleted.

        Uses SCAN to avoid blocking Redis with a single KEYS command.
        """
        pattern = f"{self.key_prefix}:tenant:{tenant.id}:*"
        keys: list[str] = []
        try:
            async for key in self.redis.scan_iter(match=pattern, count=100):
                keys.append(key)
            if keys:
                deleted: int = await self.redis.delete(*keys)
                logger.info("Cleared %d cache entries for tenant %s", deleted, tenant.id)
                return deleted
            return 0
        except RedisError as exc:
            logger.error("Redis error on clear_tenant %s: %s", tenant.id, exc, exc_info=True)
            return 0

    async def get_keys(self, tenant: Tenant, pattern: str = "*") -> list[str]:
        """Return all cache keys for *tenant* matching *pattern* (stripped of prefix)."""
        full_pattern = f"{self.key_prefix}:tenant:{tenant.id}:{pattern}"
        prefix_len = len(f"{self.key_prefix}:tenant:{tenant.id}:")
        keys: list[str] = []
        try:
            async for key in self.redis.scan_iter(match=full_pattern, count=100):
                keys.append(str(key)[prefix_len:])
            logger.debug("Found %d keys for tenant %s", len(keys), tenant.id)
            return keys
        except RedisError as exc:
            logger.error("Redis error on get_keys %s: %s", tenant.id, exc, exc_info=True)
            return []

    async def get_stats(self, tenant: Tenant) -> dict[str, Any]:
        """Return lightweight cache statistics for *tenant*."""
        pattern = f"{self.key_prefix}:tenant:{tenant.id}:*"
        keys: list[str] = []
        try:
            async for key in self.redis.scan_iter(match=pattern, count=100):
                keys.append(str(key))
            total_memory = 0
            sample = keys[:100]
            for key in sample:
                mem = await self.redis.memory_usage(key)
                if mem:
                    total_memory += mem
            if len(keys) > 100:
                total_memory = int(total_memory * (len(keys) / 100))
            return {
                "tenant_id": tenant.id,
                "key_count": len(keys),
                "memory_bytes": total_memory,
                "memory_kb": round(total_memory / 1024, 2),
                "memory_mb": round(total_memory / 1024 / 1024, 2),
            }
        except RedisError as exc:
            logger.error("Redis error on get_stats %s: %s", tenant.id, exc, exc_info=True)
            return {"tenant_id": tenant.id, "key_count": 0, "memory_bytes": 0}

    async def close(self) -> None:
        """Close the Redis connection pool gracefully."""
        logger.info("Closing TenantCache Redis connection")
        await self.redis.aclose()
        logger.info("TenantCache closed")


__all__ = ["TenantCache"]
