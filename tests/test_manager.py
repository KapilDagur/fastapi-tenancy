"""TenancyManager lifecycle, middleware, health check, and metrics tests.

API contract verified against manager.py v0.2.0:
  - TenancyManager(config, *, tenant_store=None, resolver=None, isolation_provider=None)
    NO ``app`` argument.
  - No setup_middleware() method (removed in v0.2.0).
  - create_lifespan(config, ...) is a @staticmethod returning a lifespan callable.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi_tenancy.core.exceptions import ConfigurationError
from fastapi_tenancy.core.types import Tenant
from fastapi_tenancy.storage.memory import InMemoryTenantStore


def _make_config(**kwargs):
    from fastapi_tenancy.core.config import TenancyConfig
    defaults = dict(
        database_url="sqlite+aiosqlite:///:memory:",
        resolution_strategy="header",
        isolation_strategy="rls",
    )
    defaults.update(kwargs)
    return TenancyConfig(**defaults)


def _make_manager(config=None, store=None, resolver=None, isolation=None):
    from fastapi_tenancy.manager import TenancyManager
    return TenancyManager(
        config or _make_config(),
        tenant_store=store,
        resolver=resolver,
        isolation_provider=isolation,
    )


def _make_full_manager(config=None):
    store = InMemoryTenantStore()
    resolver = AsyncMock()
    isolation = AsyncMock()
    isolation.initialize = AsyncMock()
    isolation.close = AsyncMock()
    return _make_manager(config=config, store=store, resolver=resolver, isolation=isolation)


class TestConstruction:

    def test_creates_without_error(self) -> None:
        m = _make_manager()
        assert m._initialized is False

    def test_stores_config(self) -> None:
        cfg = _make_config()
        m = _make_manager(config=cfg)
        assert m.config is cfg

    def test_custom_store_stored(self) -> None:
        store = InMemoryTenantStore()
        m = _make_manager(store=store)
        assert m._custom_store is store

    def test_custom_resolver_stored(self) -> None:
        resolver = MagicMock()
        m = _make_manager(resolver=resolver)
        assert m._custom_resolver is resolver

    def test_custom_isolation_stored(self) -> None:
        isolation = MagicMock()
        m = _make_manager(isolation=isolation)
        assert m._custom_isolation is isolation

    def test_hybrid_without_premium_tenants_raises(self) -> None:
        cfg = _make_config(isolation_strategy="hybrid")
        with pytest.raises(ConfigurationError):
            _make_manager(config=cfg)

    def test_is_not_initialized(self) -> None:
        m = _make_manager()
        assert not m._initialized

    def test_no_app_argument(self) -> None:
        import inspect
        from fastapi_tenancy.manager import TenancyManager
        sig = inspect.signature(TenancyManager.__init__)
        assert "app" not in sig.parameters


class TestLifecycle:

    @pytest.mark.asyncio
    async def test_initialize_sets_flag(self) -> None:
        m = _make_full_manager()
        await m.initialize()
        assert m._initialized is True

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self) -> None:
        m = _make_full_manager()
        await m.initialize()
        await m.initialize()
        assert m._initialized is True

    @pytest.mark.asyncio
    async def test_shutdown_clears_flag(self) -> None:
        m = _make_full_manager()
        await m.initialize()
        await m.shutdown()
        assert m._initialized is False

    @pytest.mark.asyncio
    async def test_shutdown_before_init_is_noop(self) -> None:
        m = _make_manager()
        await m.shutdown()

    @pytest.mark.asyncio
    async def test_initialize_calls_store_initialize(self) -> None:
        store = AsyncMock()
        store.initialize = AsyncMock()
        store.count = AsyncMock(return_value=1)
        store.close = AsyncMock()
        resolver = AsyncMock()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        m = _make_manager(store=store, resolver=resolver, isolation=isolation)
        await m.initialize()
        store.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        m = _make_full_manager()
        async with m as entered:
            assert entered is m
            assert m._initialized is True
        assert m._initialized is False

    @pytest.mark.asyncio
    async def test_shutdown_calls_store_close(self) -> None:
        store = AsyncMock()
        store.initialize = AsyncMock()
        store.count = AsyncMock(return_value=0)
        store.create = AsyncMock()
        store.close = AsyncMock()
        resolver = AsyncMock()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        isolation.close = AsyncMock()
        m = _make_manager(store=store, resolver=resolver, isolation=isolation)
        await m.initialize()
        await m.shutdown()
        store.close.assert_called_once()


class TestHealthAndMetrics:

    @pytest.mark.asyncio
    async def test_health_check_returns_dict(self) -> None:
        m = _make_full_manager()
        await m.initialize()
        result = await m.health_check()
        assert "status" in result
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_unhealthy_on_store_error(self) -> None:
        store = AsyncMock()
        store.initialize = AsyncMock()
        store.count = AsyncMock(side_effect=Exception("db down"))
        store.close = AsyncMock()
        resolver = AsyncMock()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        m = _make_manager(store=store, resolver=resolver, isolation=isolation)
        await m.initialize()
        result = await m.health_check()
        assert result["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_get_metrics_returns_dict(self) -> None:
        m = _make_full_manager()
        await m.initialize()
        metrics = await m.get_metrics()
        assert "total_tenants" in metrics
        assert "initialized" in metrics
        assert metrics["initialized"] is True

    @pytest.mark.asyncio
    async def test_get_metrics_counts(self) -> None:
        store = InMemoryTenantStore()
        t = Tenant(id="t1", identifier="acme", name="Acme")
        await store.create(t)
        resolver = AsyncMock()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        m = _make_manager(store=store, resolver=resolver, isolation=isolation)
        await m.initialize()
        metrics = await m.get_metrics()
        assert metrics["total_tenants"] == 1
        assert metrics["active_tenants"] == 1


class TestTenantScope:

    @pytest.mark.asyncio
    async def test_tenant_scope_sets_context(self) -> None:
        from fastapi_tenancy.core.context import TenantContext
        store = InMemoryTenantStore()
        t = Tenant(id="t1", identifier="acme", name="Acme")
        await store.create(t)
        resolver = AsyncMock()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        m = _make_manager(store=store, resolver=resolver, isolation=isolation)
        await m.initialize()
        async with m.tenant_scope("t1") as yielded:
            assert yielded.id == "t1"
            assert TenantContext.get().id == "t1"

    @pytest.mark.asyncio
    async def test_tenant_scope_clears_on_exit(self) -> None:
        from fastapi_tenancy.core.context import TenantContext
        from fastapi_tenancy.core.exceptions import TenantNotFoundError
        store = InMemoryTenantStore()
        t = Tenant(id="t1", identifier="acme", name="Acme")
        await store.create(t)
        resolver = AsyncMock()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        m = _make_manager(store=store, resolver=resolver, isolation=isolation)
        await m.initialize()
        async with m.tenant_scope("t1"):
            pass
        with pytest.raises(TenantNotFoundError):
            TenantContext.get()


class TestCreateLifespan:

    def test_create_lifespan_returns_callable(self) -> None:
        from fastapi_tenancy.manager import TenancyManager
        lifespan = TenancyManager.create_lifespan(_make_config())
        assert callable(lifespan)

    def test_create_lifespan_accepts_overrides(self) -> None:
        from fastapi_tenancy.manager import TenancyManager
        lifespan = TenancyManager.create_lifespan(
            _make_config(),
            tenant_store=InMemoryTenantStore(),
            skip_paths=["/health"],
            debug_headers=True,
        )
        assert callable(lifespan)

    @pytest.mark.asyncio
    async def test_create_lifespan_runs(self) -> None:
        from fastapi_tenancy.manager import TenancyManager
        cfg = _make_config()
        store = InMemoryTenantStore()
        resolver = AsyncMock()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        isolation.close = AsyncMock()
        lifespan = TenancyManager.create_lifespan(
            cfg, tenant_store=store, isolation_provider=isolation, resolver=resolver
        )
        mock_app = MagicMock()
        mock_app.state = MagicMock()
        mock_app.add_middleware = MagicMock()
        async with lifespan(mock_app):
            mock_app.add_middleware.assert_called_once()
            assert mock_app.state.tenancy_manager is not None


class TestInternalInit:

    @pytest.mark.asyncio
    async def test_initialize_storage_default(self) -> None:
        with patch("fastapi_tenancy.manager.SQLAlchemyTenantStore") as MockStore:
            from fastapi_tenancy.manager import TenancyManager
            mock_store = AsyncMock()
            mock_store.initialize = AsyncMock()
            mock_store.count = AsyncMock(return_value=0)
            mock_store.create = AsyncMock()
            MockStore.return_value = mock_store
            m = TenancyManager(
                _make_config(),
                resolver=AsyncMock(),
                isolation_provider=AsyncMock(initialize=AsyncMock()),
            )
            await m.initialize()
            MockStore.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_resolver_factory(self) -> None:
        store = InMemoryTenantStore()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        with patch("fastapi_tenancy.resolution.factory.ResolverFactory.create") as mock_create:
            mock_create.return_value = AsyncMock()
            from fastapi_tenancy.manager import TenancyManager
            m = TenancyManager(
                _make_config(),
                tenant_store=store,
                isolation_provider=isolation,
            )
            await m.initialize()
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_isolation_factory(self) -> None:
        store = InMemoryTenantStore()
        with patch("fastapi_tenancy.isolation.factory.IsolationProviderFactory.create") as mock_create:
            mock_create.return_value = AsyncMock()
            from fastapi_tenancy.manager import TenancyManager
            m = TenancyManager(
                _make_config(),
                tenant_store=store,
                resolver=AsyncMock(),
            )
            await m.initialize()
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_default_tenant_when_registration_enabled(self) -> None:
        store = InMemoryTenantStore()
        resolver = AsyncMock()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        cfg = _make_config(allow_tenant_registration=True)
        m = _make_manager(config=cfg, store=store, resolver=resolver, isolation=isolation)
        await m.initialize()
        count = await store.count()
        assert count == 1

    @pytest.mark.asyncio
    async def test_no_default_tenant_when_registration_disabled(self) -> None:
        store = InMemoryTenantStore()
        resolver = AsyncMock()
        isolation = AsyncMock()
        isolation.initialize = AsyncMock()
        cfg = _make_config(allow_tenant_registration=False)
        m = _make_manager(config=cfg, store=store, resolver=resolver, isolation=isolation)
        await m.initialize()
        assert await store.count() == 0
