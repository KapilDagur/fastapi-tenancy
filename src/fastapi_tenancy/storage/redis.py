"""Redis write-through cache layer for tenant storage.

Fix from v0.1.0
---------------
- model_dump() + json.dumps() → model_dump_json() / model_validate_json()
  Pydantic v2's model_dump() returns Python datetime objects that json.dumps()
  cannot serialise.  model_dump_json() produces an ISO-8601 string that round-
  trips cleanly through model_validate_json().
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from redis import asyncio as aioredis

from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.storage.tenant_store import TenantStore

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


class RedisTenantStore(TenantStore):
    """Redis write-through cache on top of a primary :class:`TenantStore`.

    Reads are served from Redis when warm; writes always go to the primary
    store first, then the cache is refreshed.

    Architecture::

        Application → RedisTenantStore → Redis (hot path)
                                       ↘ PostgreSQLTenantStore (cold path / writes)

    Example
    -------
    .. code-block:: python

        primary = PostgreSQLTenantStore(database_url="postgresql+asyncpg://...")
        cache   = RedisTenantStore(
            redis_url="redis://localhost:6379/0",
            primary_store=primary,
            ttl=1800,
        )
        tenant = await cache.get_by_identifier("acme-corp")  # sub-millisecond on hit
    """

    def __init__(
        self,
        redis_url: str,
        primary_store: TenantStore,
        ttl: int = 3600,
        key_prefix: str = "tenant",
    ) -> None:
        self.primary_store = primary_store
        self.ttl = ttl
        self.key_prefix = key_prefix
        self.redis: aioredis.Redis = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=False,
        )
        logger.info("RedisTenantStore initialised ttl=%ds prefix=%s", ttl, key_prefix)

    def _id_key(self, tenant_id: str) -> str:
        return f"{self.key_prefix}:id:{tenant_id}"

    def _slug_key(self, identifier: str) -> str:
        return f"{self.key_prefix}:identifier:{identifier}"

    def _serialize(self, tenant: Tenant) -> bytes:
        """Serialise using Pydantic's own JSON encoder (handles datetime, enums …)."""
        return tenant.model_dump_json().encode("utf-8")

    def _deserialize(self, data: bytes) -> Tenant:
        """Deserialise using Pydantic's model_validate_json for type safety."""
        return Tenant.model_validate_json(data.decode("utf-8"))

    async def _cache_tenant(self, tenant: Tenant) -> None:
        serialised = self._serialize(tenant)
        pipe = self.redis.pipeline()
        pipe.setex(self._id_key(tenant.id), self.ttl, serialised)
        pipe.setex(self._slug_key(tenant.identifier), self.ttl, serialised)
        await pipe.execute()
        logger.debug("Cached tenant id=%s", tenant.id)

    async def _invalidate(self, tenant_id: str, identifier: str) -> None:
        await self.redis.delete(self._id_key(tenant_id), self._slug_key(identifier))
        logger.debug("Invalidated cache tenant id=%s", tenant_id)

    async def get_by_id(self, tenant_id: str) -> Tenant:
        cached = await self.redis.get(self._id_key(tenant_id))
        if cached:
            logger.debug("Cache hit id=%s", tenant_id)
            return self._deserialize(cached)
        logger.debug("Cache miss id=%s", tenant_id)
        tenant = await self.primary_store.get_by_id(tenant_id)
        await self._cache_tenant(tenant)
        return tenant

    async def get_by_identifier(self, identifier: str) -> Tenant:
        cached = await self.redis.get(self._slug_key(identifier))
        if cached:
            logger.debug("Cache hit identifier=%s", identifier)
            return self._deserialize(cached)
        logger.debug("Cache miss identifier=%s", identifier)
        tenant = await self.primary_store.get_by_identifier(identifier)
        await self._cache_tenant(tenant)
        return tenant

    async def create(self, tenant: Tenant) -> Tenant:
        created = await self.primary_store.create(tenant)
        await self._cache_tenant(created)
        logger.info("Created+cached tenant id=%s", created.id)
        return created

    async def update(self, tenant: Tenant) -> Tenant:
        # Fetch old identifier so we can invalidate the old slug key
        try:
            old = await self.primary_store.get_by_id(tenant.id)
        except TenantNotFoundError:
            raise
        updated = await self.primary_store.update(tenant)
        await self._invalidate(old.id, old.identifier)
        await self._cache_tenant(updated)
        logger.info("Updated+re-cached tenant id=%s", updated.id)
        return updated

    async def delete(self, tenant_id: str) -> None:
        try:
            tenant = await self.primary_store.get_by_id(tenant_id)
        except TenantNotFoundError:
            raise
        await self.primary_store.delete(tenant_id)
        await self._invalidate(tenant.id, tenant.identifier)
        logger.info("Deleted+invalidated cache tenant id=%s", tenant_id)

    async def list(
        self, skip: int = 0, limit: int = 100, status: TenantStatus | None = None
    ) -> Iterable[Tenant]:
        # List operations always hit the primary (complex query, not worth caching)
        return await self.primary_store.list(skip=skip, limit=limit, status=status)

    async def count(self, status: TenantStatus | None = None) -> int:
        return await self.primary_store.count(status=status)

    async def exists(self, tenant_id: str) -> bool:
        if await self.redis.exists(self._id_key(tenant_id)):
            return True
        return await self.primary_store.exists(tenant_id)

    async def set_status(self, tenant_id: str, status: TenantStatus) -> Tenant:
        try:
            old = await self.primary_store.get_by_id(tenant_id)
        except TenantNotFoundError:
            raise
        updated = await self.primary_store.set_status(tenant_id, status)
        await self._invalidate(old.id, old.identifier)
        await self._cache_tenant(updated)
        return updated

    async def update_metadata(
        self, tenant_id: str, metadata: dict[str, Any]
    ) -> Tenant:
        try:
            old = await self.primary_store.get_by_id(tenant_id)
        except TenantNotFoundError:
            raise
        updated = await self.primary_store.update_metadata(tenant_id, metadata)
        await self._invalidate(old.id, old.identifier)
        await self._cache_tenant(updated)
        return updated

    async def invalidate_all(self) -> int:
        """Invalidate every cache entry owned by this store.

        Uses SCAN to avoid blocking Redis with a single KEYS command.
        Returns the number of keys deleted.
        """
        pattern = f"{self.key_prefix}:*"
        keys: list[bytes] = []
        async for key in self.redis.scan_iter(match=pattern, count=100):
            keys.append(key)
        if keys:
            deleted: int = await self.redis.delete(*keys)
            logger.info("Invalidated %d cache entries", deleted)
            return deleted
        return 0

    async def get_cache_stats(self) -> dict[str, Any]:
        """Return lightweight cache statistics (key count, TTL config)."""
        pattern = f"{self.key_prefix}:*"
        count = 0
        async for _ in self.redis.scan_iter(match=pattern, count=100):
            count += 1
        return {
            "total_keys": count,
            "ttl_seconds": self.ttl,
            "key_prefix": self.key_prefix,
        }

    async def close(self) -> None:
        """Close the Redis connection pool."""
        logger.info("Closing RedisTenantStore")
        await self.redis.aclose()
        logger.info("RedisTenantStore closed")


__all__ = ["RedisTenantStore"]
