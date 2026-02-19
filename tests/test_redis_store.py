"""Unit tests for RedisTenantStore — serialisation fix verification."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import IsolationStrategy, Tenant, TenantStatus
from fastapi_tenancy.storage.redis import RedisTenantStore


def make_tenant(**kwargs) -> Tenant:
    defaults = dict(id="t1", identifier="acme-corp", name="Acme Corp")
    defaults.update(kwargs)
    return Tenant(**defaults)


@pytest.fixture
def primary_store() -> MagicMock:
    store = MagicMock()
    return store


@pytest.fixture
def mock_redis() -> MagicMock:
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.exists = AsyncMock(return_value=0)
    redis.pipeline = MagicMock()
    redis.aclose = AsyncMock()
    return redis


@pytest.fixture
def redis_store(primary_store: MagicMock, mock_redis: MagicMock) -> RedisTenantStore:
    with patch("fastapi_tenancy.storage.redis.aioredis.from_url", return_value=mock_redis):
        store = RedisTenantStore(
            redis_url="redis://localhost:6379/0",
            primary_store=primary_store,
            ttl=60,
        )
    store.redis = mock_redis
    return store


class TestSerialization:
    """The datetime serialisation fix is the most critical regression to test."""

    def test_serialize_basic_tenant(self, redis_store: RedisTenantStore) -> None:
        tenant = make_tenant()
        raw = redis_store._serialize(tenant)
        assert isinstance(raw, bytes)
        # Must be valid JSON
        import json
        data = json.loads(raw.decode())
        assert data["id"] == "t1"
        assert data["identifier"] == "acme-corp"

    def test_serialize_tenant_with_timestamps(self, redis_store: RedisTenantStore) -> None:
        """Serialisation must not crash on datetime fields."""
        tenant = make_tenant(
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        raw = redis_store._serialize(tenant)
        assert isinstance(raw, bytes)

    def test_roundtrip(self, redis_store: RedisTenantStore) -> None:
        """model_dump_json → model_validate_json must be lossless."""
        tenant = make_tenant(
            status=TenantStatus.ACTIVE,
            metadata={"plan": "enterprise"},
        )
        raw = redis_store._serialize(tenant)
        restored = redis_store._deserialize(raw)
        assert restored.id == tenant.id
        assert restored.identifier == tenant.identifier
        assert restored.status == tenant.status
        assert restored.metadata == tenant.metadata

    def test_roundtrip_with_isolation_strategy(self, redis_store: RedisTenantStore) -> None:
        tenant = make_tenant(isolation_strategy=IsolationStrategy.SCHEMA)
        raw = redis_store._serialize(tenant)
        restored = redis_store._deserialize(raw)
        assert restored.isolation_strategy == IsolationStrategy.SCHEMA


class TestGetById:

    @pytest.mark.asyncio
    async def test_cache_miss_calls_primary(
        self,
        redis_store: RedisTenantStore,
        primary_store: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        tenant = make_tenant()
        mock_redis.get = AsyncMock(return_value=None)
        primary_store.get_by_id = AsyncMock(return_value=tenant)

        pipe = MagicMock()
        pipe.setex = MagicMock()
        pipe.execute = AsyncMock(return_value=[True, True])
        mock_redis.pipeline = MagicMock(return_value=pipe)

        result = await redis_store.get_by_id("t1")
        primary_store.get_by_id.assert_called_once_with("t1")
        assert result.id == "t1"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_primary(
        self,
        redis_store: RedisTenantStore,
        primary_store: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        tenant = make_tenant()
        mock_redis.get = AsyncMock(return_value=redis_store._serialize(tenant))
        primary_store.get_by_id = AsyncMock()

        result = await redis_store.get_by_id("t1")
        primary_store.get_by_id.assert_not_called()
        assert result.id == "t1"


class TestCreate:

    @pytest.mark.asyncio
    async def test_create_calls_primary_and_caches(
        self,
        redis_store: RedisTenantStore,
        primary_store: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        tenant = make_tenant()
        primary_store.create = AsyncMock(return_value=tenant)

        pipe = MagicMock()
        pipe.setex = MagicMock()
        pipe.execute = AsyncMock(return_value=[True, True])
        mock_redis.pipeline = MagicMock(return_value=pipe)

        result = await redis_store.create(tenant)
        primary_store.create.assert_called_once()
        assert result.id == tenant.id


class TestDelete:

    @pytest.mark.asyncio
    async def test_delete_invalidates_cache(
        self,
        redis_store: RedisTenantStore,
        primary_store: MagicMock,
        mock_redis: MagicMock,
    ) -> None:
        tenant = make_tenant()
        primary_store.get_by_id = AsyncMock(return_value=tenant)
        primary_store.delete = AsyncMock()
        mock_redis.delete = AsyncMock(return_value=2)

        await redis_store.delete("t1")
        primary_store.delete.assert_called_once_with("t1")
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_not_found_propagates(
        self,
        redis_store: RedisTenantStore,
        primary_store: MagicMock,
    ) -> None:
        primary_store.get_by_id = AsyncMock(side_effect=TenantNotFoundError(identifier="t1"))
        with pytest.raises(TenantNotFoundError):
            await redis_store.delete("t1")


class TestClose:

    @pytest.mark.asyncio
    async def test_close_calls_aclose(
        self,
        redis_store: RedisTenantStore,
        mock_redis: MagicMock,
    ) -> None:
        await redis_store.close()
        mock_redis.aclose.assert_called_once()
