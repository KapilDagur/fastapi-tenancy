"""TenantCache — full coverage using a mocked Redis client."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from fastapi_tenancy.core.types import Tenant


def make_tenant(slug: str = "acme-corp") -> Tenant:
    return Tenant(id="t1", identifier=slug, name=slug.title())


def make_cache(url: str = "redis://localhost:6379/0"):
    with patch("fastapi_tenancy.cache.tenant_cache.aioredis.from_url") as mock_from_url:
        mock_redis = AsyncMock()
        mock_from_url.return_value = mock_redis
        from fastapi_tenancy.cache.tenant_cache import TenantCache
        cache = TenantCache(url, default_ttl=300, key_prefix="test")
    return cache, mock_redis


class TestKeyBuilding:
    def test_make_key_format(self) -> None:
        cache, _ = make_cache()
        t = make_tenant()
        key = cache._make_key(t, "user_count")
        assert key == "test:tenant:t1:user_count"

    def test_make_key_different_tenants(self) -> None:
        cache, _ = make_cache()
        t1 = Tenant(id="a", identifier="a", name="A")
        t2 = Tenant(id="b", identifier="b", name="B")
        assert cache._make_key(t1, "k") != cache._make_key(t2, "k")


class TestSerialize:
    def test_string_passthrough(self) -> None:
        from fastapi_tenancy.cache.tenant_cache import TenantCache
        assert TenantCache._serialize("hello") == "hello"

    def test_dict_to_json(self) -> None:
        from fastapi_tenancy.cache.tenant_cache import TenantCache
        result = TenantCache._serialize({"key": "val"})
        assert json.loads(result) == {"key": "val"}

    def test_int_to_json(self) -> None:
        from fastapi_tenancy.cache.tenant_cache import TenantCache
        assert TenantCache._serialize(42) == "42"

    def test_datetime_to_iso(self) -> None:
        from fastapi_tenancy.cache.tenant_cache import TenantCache
        dt = datetime(2024, 1, 15, 12, 0, 0)
        result = TenantCache._serialize(dt)
        assert "2024-01-15" in result

    def test_deserialize_json(self) -> None:
        from fastapi_tenancy.cache.tenant_cache import TenantCache
        assert TenantCache._deserialize('{"a": 1}') == {"a": 1}

    def test_deserialize_plain_string(self) -> None:
        from fastapi_tenancy.cache.tenant_cache import TenantCache
        assert TenantCache._deserialize("hello") == "hello"

    def test_deserialize_invalid_json_returns_raw(self) -> None:
        from fastapi_tenancy.cache.tenant_cache import TenantCache
        assert TenantCache._deserialize("not-json") == "not-json"


class TestGet:
    @pytest.mark.asyncio
    async def test_get_hit(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.get = AsyncMock(return_value='"hello"')
        t = make_tenant()
        result = await cache.get(t, "mykey")
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_get_miss(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.get = AsyncMock(return_value=None)
        t = make_tenant()
        result = await cache.get(t, "mykey")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_redis_error_returns_none(self) -> None:
        from redis.exceptions import RedisError
        cache, mock_redis = make_cache()
        mock_redis.get = AsyncMock(side_effect=RedisError("conn refused"))
        t = make_tenant()
        result = await cache.get(t, "mykey")
        assert result is None


class TestSet:
    @pytest.mark.asyncio
    async def test_set_returns_true_on_success(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.setex = AsyncMock(return_value=True)
        t = make_tenant()
        result = await cache.set(t, "k", {"data": 1})
        assert result is True
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_uses_default_ttl(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.setex = AsyncMock(return_value=True)
        t = make_tenant()
        await cache.set(t, "k", "v")
        args = mock_redis.setex.call_args
        assert args[0][1] == 300  # default_ttl=300

    @pytest.mark.asyncio
    async def test_set_custom_ttl(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.setex = AsyncMock(return_value=True)
        t = make_tenant()
        await cache.set(t, "k", "v", ttl=60)
        args = mock_redis.setex.call_args
        assert args[0][1] == 60

    @pytest.mark.asyncio
    async def test_set_redis_error_returns_false(self) -> None:
        from redis.exceptions import RedisError
        cache, mock_redis = make_cache()
        mock_redis.setex = AsyncMock(side_effect=RedisError("timeout"))
        t = make_tenant()
        result = await cache.set(t, "k", "v")
        assert result is False


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_existing_key(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.delete = AsyncMock(return_value=1)
        t = make_tenant()
        result = await cache.delete(t, "k")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_missing_key(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.delete = AsyncMock(return_value=0)
        t = make_tenant()
        result = await cache.delete(t, "k")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_redis_error_returns_false(self) -> None:
        from redis.exceptions import RedisError
        cache, mock_redis = make_cache()
        mock_redis.delete = AsyncMock(side_effect=RedisError("err"))
        result = await cache.delete(make_tenant(), "k")
        assert result is False


class TestExists:
    @pytest.mark.asyncio
    async def test_exists_true(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.exists = AsyncMock(return_value=1)
        assert await cache.exists(make_tenant(), "k") is True

    @pytest.mark.asyncio
    async def test_exists_false(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.exists = AsyncMock(return_value=0)
        assert await cache.exists(make_tenant(), "k") is False

    @pytest.mark.asyncio
    async def test_exists_redis_error(self) -> None:
        from redis.exceptions import RedisError
        cache, mock_redis = make_cache()
        mock_redis.exists = AsyncMock(side_effect=RedisError("err"))
        assert await cache.exists(make_tenant(), "k") is False


class TestGetTtl:
    @pytest.mark.asyncio
    async def test_get_ttl_returns_value(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.ttl = AsyncMock(return_value=250)
        assert await cache.get_ttl(make_tenant(), "k") == 250

    @pytest.mark.asyncio
    async def test_get_ttl_redis_error(self) -> None:
        from redis.exceptions import RedisError
        cache, mock_redis = make_cache()
        mock_redis.ttl = AsyncMock(side_effect=RedisError("err"))
        assert await cache.get_ttl(make_tenant(), "k") == -2


class TestIncrement:
    @pytest.mark.asyncio
    async def test_increment_default(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.incrby = AsyncMock(return_value=5)
        result = await cache.increment(make_tenant(), "counter")
        assert result == 5
        mock_redis.incrby.assert_called_once_with(pytest.approx, 1)

    @pytest.mark.asyncio
    async def test_increment_amount(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.incrby = AsyncMock(return_value=10)
        result = await cache.increment(make_tenant(), "counter", amount=5)
        assert result == 10

    @pytest.mark.asyncio
    async def test_increment_redis_error_raises(self) -> None:
        from redis.exceptions import RedisError

        from fastapi_tenancy.core.exceptions import TenancyError
        cache, mock_redis = make_cache()
        mock_redis.incrby = AsyncMock(side_effect=RedisError("err"))
        with pytest.raises(TenancyError):
            await cache.increment(make_tenant(), "counter")


class TestClearTenant:
    @pytest.mark.asyncio
    async def test_clear_with_keys(self) -> None:
        cache, mock_redis = make_cache()
        async def mock_scan(*a, **kw):
            yield "test:tenant:t1:k1"
            yield "test:tenant:t1:k2"
        mock_redis.scan_iter = mock_scan
        mock_redis.delete = AsyncMock(return_value=2)
        result = await cache.clear_tenant(make_tenant())
        assert result == 2

    @pytest.mark.asyncio
    async def test_clear_no_keys(self) -> None:
        cache, mock_redis = make_cache()
        async def mock_scan(*a, **kw):
            return
            yield  # make it a generator
        mock_redis.scan_iter = mock_scan
        result = await cache.clear_tenant(make_tenant())
        assert result == 0

    @pytest.mark.asyncio
    async def test_clear_redis_error(self) -> None:
        from redis.exceptions import RedisError
        cache, mock_redis = make_cache()
        async def mock_scan(*a, **kw):
            raise RedisError("err")
            yield
        mock_redis.scan_iter = mock_scan
        result = await cache.clear_tenant(make_tenant())
        assert result == 0


class TestGetKeys:
    @pytest.mark.asyncio
    async def test_get_keys_strips_prefix(self) -> None:
        cache, mock_redis = make_cache()
        async def mock_scan(*a, **kw):
            yield "test:tenant:t1:user_count"
            yield "test:tenant:t1:session"
        mock_redis.scan_iter = mock_scan
        keys = await cache.get_keys(make_tenant())
        assert "user_count" in keys
        assert "session" in keys

    @pytest.mark.asyncio
    async def test_get_keys_redis_error(self) -> None:
        from redis.exceptions import RedisError
        cache, mock_redis = make_cache()
        async def mock_scan(*a, **kw):
            raise RedisError("err")
            yield
        mock_redis.scan_iter = mock_scan
        result = await cache.get_keys(make_tenant())
        assert result == []


class TestGetStats:
    @pytest.mark.asyncio
    async def test_get_stats_returns_dict(self) -> None:
        cache, mock_redis = make_cache()
        async def mock_scan(*a, **kw):
            yield "test:tenant:t1:k1"
        mock_redis.scan_iter = mock_scan
        mock_redis.memory_usage = AsyncMock(return_value=1024)
        stats = await cache.get_stats(make_tenant())
        assert stats["tenant_id"] == "t1"
        assert stats["key_count"] == 1

    @pytest.mark.asyncio
    async def test_get_stats_redis_error(self) -> None:
        from redis.exceptions import RedisError
        cache, mock_redis = make_cache()
        async def mock_scan(*a, **kw):
            raise RedisError("err")
            yield
        mock_redis.scan_iter = mock_scan
        stats = await cache.get_stats(make_tenant())
        assert stats["key_count"] == 0


class TestClose:
    @pytest.mark.asyncio
    async def test_close_calls_aclose(self) -> None:
        cache, mock_redis = make_cache()
        mock_redis.aclose = AsyncMock()
        await cache.close()
        mock_redis.aclose.assert_called_once()
