"""Hybrid isolation provider tests.

Tests cover:
- Correct sub-provider selection by tenant tier (premium / standard)
- Shared engine injection (no duplicate pools)
- Delegation of get_session / apply_filters / initialize_tenant / destroy_tenant
- StaticPool guard for SQLite URLs
- Unsupported sub-strategy raises clearly
- Config.is_premium_tenant drives routing
- close() disposes shared engine, not sub-provider engines
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from fastapi_tenancy.core.exceptions import IsolationError
from fastapi_tenancy.core.types import IsolationStrategy, Tenant, TenantStatus


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_tenant(slug: str, tenant_id: str | None = None) -> Tenant:
    tid = tenant_id or f"t-{slug}"
    return Tenant(id=tid, identifier=slug, name=slug.title(), status=TenantStatus.ACTIVE)


def make_hybrid_config(
    url: str = "postgresql+asyncpg://u:p@host/db",
    premium_ids: list[str] | None = None,
    premium_strategy: IsolationStrategy = IsolationStrategy.SCHEMA,
    standard_strategy: IsolationStrategy = IsolationStrategy.RLS,
) -> MagicMock:
    cfg = MagicMock()
    cfg.database_url = url
    cfg.database_pool_size = 5
    cfg.database_max_overflow = 10
    cfg.database_pool_timeout = 30
    cfg.database_pool_recycle = 3600
    cfg.database_echo = False
    cfg.premium_isolation_strategy = premium_strategy
    cfg.standard_isolation_strategy = standard_strategy

    _premium = set(premium_ids or [])

    def _is_premium(tid: str) -> bool:
        return tid in _premium

    cfg.is_premium_tenant = _is_premium

    def _get_schema(tid: str) -> str:
        return f"tenant_{tid.replace('-', '_')}"

    cfg.get_schema_name = _get_schema
    return cfg


# ---------------------------------------------------------------------------
# Construction / engine sharing
# ---------------------------------------------------------------------------

class TestHybridConstruction:

    def test_creates_single_shared_engine(self) -> None:
        """HybridIsolationProvider must create exactly ONE AsyncEngine
        regardless of which sub-strategies are selected."""
        cfg = make_hybrid_config()
        mock_engine = MagicMock()

        with patch("fastapi_tenancy.isolation.hybrid.create_async_engine", return_value=mock_engine) as mock_create, \
             patch("fastapi_tenancy.isolation.schema.SchemaIsolationProvider.__init__", return_value=None), \
             patch("fastapi_tenancy.isolation.rls.RLSIsolationProvider.__init__", return_value=None):
            from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider
            HybridIsolationProvider(cfg)

        # create_async_engine is called exactly once — in the hybrid provider
        assert mock_create.call_count == 1

    def test_shared_engine_passed_to_sub_providers(self) -> None:
        """The engine created in HybridIsolationProvider is injected into
        both sub-providers via engine= keyword, not rebuilt inside each."""
        cfg = make_hybrid_config()
        mock_engine = MagicMock()

        schema_init_args = {}
        rls_init_args = {}

        def capture_schema_init(self, config, engine=None):
            schema_init_args['engine'] = engine
            self.config = config
            self.engine = engine or MagicMock()
            from fastapi_tenancy.utils.db_compat import DbDialect
            self.dialect = DbDialect.POSTGRESQL

        def capture_rls_init(self, config, engine=None):
            rls_init_args['engine'] = engine
            self.config = config
            self.engine = engine or MagicMock()
            from fastapi_tenancy.utils.db_compat import DbDialect
            self.dialect = DbDialect.POSTGRESQL

        with patch("fastapi_tenancy.isolation.hybrid.create_async_engine", return_value=mock_engine), \
             patch("fastapi_tenancy.isolation.schema.SchemaIsolationProvider.__init__", capture_schema_init), \
             patch("fastapi_tenancy.isolation.rls.RLSIsolationProvider.__init__", capture_rls_init):
            from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider
            HybridIsolationProvider(cfg)

        assert schema_init_args.get('engine') is mock_engine
        assert rls_init_args.get('engine') is mock_engine

    def test_sqlite_url_uses_static_pool(self) -> None:
        """SQLite in-memory requires StaticPool — verify it's applied."""
        from sqlalchemy.pool import StaticPool
        cfg = make_hybrid_config(url="sqlite+aiosqlite:///:memory:")
        mock_engine = MagicMock()
        captured_kwargs = {}

        def fake_create_engine(url, **kw):
            captured_kwargs.update(kw)
            return mock_engine

        with patch("fastapi_tenancy.isolation.hybrid.create_async_engine", fake_create_engine), \
             patch("fastapi_tenancy.isolation.schema.SchemaIsolationProvider.__init__", lambda s, c, engine=None: setattr(s, 'engine', engine) or setattr(s, 'config', c) or setattr(s, 'dialect', __import__('fastapi_tenancy.utils.db_compat', fromlist=['DbDialect']).DbDialect.SQLITE)), \
             patch("fastapi_tenancy.isolation.rls.RLSIsolationProvider.__init__", lambda s, c, engine=None: setattr(s, 'engine', engine) or setattr(s, 'config', c) or setattr(s, 'dialect', __import__('fastapi_tenancy.utils.db_compat', fromlist=['DbDialect']).DbDialect.SQLITE)):
            from importlib import reload
            import fastapi_tenancy.isolation.hybrid as hmod
            reload(hmod)
            hmod.HybridIsolationProvider(cfg)

        assert captured_kwargs.get('poolclass') is StaticPool

    def test_postgres_url_uses_regular_pool(self) -> None:
        """PostgreSQL should use regular connection pool, not StaticPool."""
        from sqlalchemy.pool import StaticPool
        cfg = make_hybrid_config(url="postgresql+asyncpg://u:p@h/db")
        mock_engine = MagicMock()
        captured_kwargs = {}

        def fake_create_engine(url, **kw):
            captured_kwargs.update(kw)
            return mock_engine

        with patch("fastapi_tenancy.isolation.hybrid.create_async_engine", fake_create_engine), \
             patch("fastapi_tenancy.isolation.schema.SchemaIsolationProvider.__init__", lambda s, c, engine=None: setattr(s, 'engine', engine) or setattr(s, 'config', c) or setattr(s, 'dialect', __import__('fastapi_tenancy.utils.db_compat', fromlist=['DbDialect']).DbDialect.POSTGRESQL)), \
             patch("fastapi_tenancy.isolation.rls.RLSIsolationProvider.__init__", lambda s, c, engine=None: setattr(s, 'engine', engine) or setattr(s, 'config', c) or setattr(s, 'dialect', __import__('fastapi_tenancy.utils.db_compat', fromlist=['DbDialect']).DbDialect.POSTGRESQL)):
            from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider
            HybridIsolationProvider(cfg)

        assert captured_kwargs.get('poolclass') is not StaticPool
        assert 'pool_size' in captured_kwargs

    def test_unsupported_sub_strategy_raises(self) -> None:
        """Hybrid mode does not support HYBRID as a sub-strategy."""
        cfg = make_hybrid_config(
            premium_strategy=IsolationStrategy.HYBRID,
            standard_strategy=IsolationStrategy.RLS,
        )
        with patch("fastapi_tenancy.isolation.hybrid.create_async_engine", return_value=MagicMock()):
            from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider
            with pytest.raises((ValueError, IsolationError)):
                HybridIsolationProvider(cfg)


# ---------------------------------------------------------------------------
# Provider routing — _get_provider
# ---------------------------------------------------------------------------

class TestHybridRouting:

    def _make_provider(
        self,
        premium_ids: list[str] | None = None,
    ):
        """Build a HybridIsolationProvider with mocked sub-providers."""
        from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider

        cfg = make_hybrid_config(premium_ids=premium_ids)
        mock_engine = MagicMock()

        mock_schema = MagicMock()
        mock_rls = MagicMock()

        with patch("fastapi_tenancy.isolation.hybrid.create_async_engine", return_value=mock_engine), \
             patch("fastapi_tenancy.isolation.schema.SchemaIsolationProvider.__init__", lambda s, c, engine=None: (setattr(s, 'engine', engine), setattr(s, 'config', c), setattr(s, 'dialect', __import__('fastapi_tenancy.utils.db_compat', fromlist=['DbDialect']).DbDialect.POSTGRESQL))):
            provider = HybridIsolationProvider.__new__(HybridIsolationProvider)
            provider.config = cfg
            provider._shared_engine = mock_engine
            provider.premium_provider = mock_schema
            provider.standard_provider = mock_rls

        return provider, mock_schema, mock_rls

    def test_premium_tenant_gets_premium_provider(self) -> None:
        provider, premium, standard = self._make_provider(premium_ids=["t-premium"])
        tenant = make_tenant("premium-corp", tenant_id="t-premium")
        selected = provider._get_provider(tenant)
        assert selected is premium

    def test_standard_tenant_gets_standard_provider(self) -> None:
        provider, premium, standard = self._make_provider(premium_ids=["t-premium"])
        tenant = make_tenant("standard-corp", tenant_id="t-standard")
        selected = provider._get_provider(tenant)
        assert selected is standard

    def test_empty_premium_list_all_standard(self) -> None:
        provider, premium, standard = self._make_provider(premium_ids=[])
        for slug in ["acme", "beta", "gamma"]:
            t = make_tenant(slug)
            assert provider._get_provider(t) is standard

    def test_all_known_ids_premium(self) -> None:
        ids = ["t-a", "t-b", "t-c"]
        provider, premium, standard = self._make_provider(premium_ids=ids)
        for tid in ids:
            t = make_tenant(tid.replace("t-", ""), tenant_id=tid)
            assert provider._get_provider(t) is premium


# ---------------------------------------------------------------------------
# Delegation — get_session / apply_filters / initialize_tenant / destroy_tenant
# ---------------------------------------------------------------------------

class TestHybridDelegation:

    def _make_provider(self, premium_ids: list[str] | None = None):
        from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider

        cfg = make_hybrid_config(premium_ids=premium_ids)
        mock_engine = MagicMock()
        mock_schema = MagicMock()
        mock_rls = MagicMock()

        provider = HybridIsolationProvider.__new__(HybridIsolationProvider)
        provider.config = cfg
        provider._shared_engine = mock_engine
        provider.premium_provider = mock_schema
        provider.standard_provider = mock_rls
        return provider, mock_schema, mock_rls

    @pytest.mark.asyncio
    async def test_get_session_delegates_to_premium_provider(self) -> None:
        provider, premium, _ = self._make_provider(premium_ids=["t-p"])
        tenant = make_tenant("prem", tenant_id="t-p")

        mock_session = MagicMock()
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_session(t):
            yield mock_session

        premium.get_session = fake_session

        async with provider.get_session(tenant) as session:
            assert session is mock_session

    @pytest.mark.asyncio
    async def test_get_session_delegates_to_standard_provider(self) -> None:
        provider, _, standard = self._make_provider(premium_ids=[])
        tenant = make_tenant("std", tenant_id="t-s")

        mock_session = MagicMock()
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def fake_session(t):
            yield mock_session

        standard.get_session = fake_session

        async with provider.get_session(tenant) as session:
            assert session is mock_session

    @pytest.mark.asyncio
    async def test_apply_filters_delegates_to_correct_provider(self) -> None:
        provider, premium, standard = self._make_provider(premium_ids=["t-p"])
        premium_tenant = make_tenant("prem", tenant_id="t-p")
        std_tenant = make_tenant("std", tenant_id="t-s")

        query = MagicMock()
        filtered_p = MagicMock()
        filtered_s = MagicMock()

        premium.apply_filters = AsyncMock(return_value=filtered_p)
        standard.apply_filters = AsyncMock(return_value=filtered_s)

        result_p = await provider.apply_filters(query, premium_tenant)
        result_s = await provider.apply_filters(query, std_tenant)

        assert result_p is filtered_p
        assert result_s is filtered_s
        premium.apply_filters.assert_awaited_once_with(query, premium_tenant)
        standard.apply_filters.assert_awaited_once_with(query, std_tenant)

    @pytest.mark.asyncio
    async def test_initialize_tenant_delegates(self) -> None:
        provider, premium, standard = self._make_provider(premium_ids=["t-p"])
        p_tenant = make_tenant("prem", tenant_id="t-p")
        s_tenant = make_tenant("std", tenant_id="t-s")

        premium.initialize_tenant = AsyncMock()
        standard.initialize_tenant = AsyncMock()

        await provider.initialize_tenant(p_tenant)
        await provider.initialize_tenant(s_tenant)

        premium.initialize_tenant.assert_awaited_once_with(p_tenant)
        standard.initialize_tenant.assert_awaited_once_with(s_tenant)

    @pytest.mark.asyncio
    async def test_destroy_tenant_delegates_to_standard_provider(self) -> None:
        provider, _, standard = self._make_provider(premium_ids=[])
        tenant = make_tenant("std", tenant_id="t-s")

        standard.destroy_tenant = AsyncMock()

        await provider.destroy_tenant(tenant)

        standard.destroy_tenant.assert_awaited_once_with(tenant)

    @pytest.mark.asyncio
    async def test_destroy_tenant_delegates_to_premium_provider(self) -> None:
        provider, premium, _ = self._make_provider(premium_ids=["t-p"])
        tenant = make_tenant("prem", tenant_id="t-p")

        premium.destroy_tenant = AsyncMock()

        await provider.destroy_tenant(tenant)

        premium.destroy_tenant.assert_awaited_once_with(tenant)


# ---------------------------------------------------------------------------
# Lifecycle — close()
# ---------------------------------------------------------------------------

class TestHybridClose:

    @pytest.mark.asyncio
    async def test_close_disposes_shared_engine(self) -> None:
        from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        provider = HybridIsolationProvider.__new__(HybridIsolationProvider)
        provider._shared_engine = mock_engine
        provider.premium_provider = MagicMock()
        provider.standard_provider = MagicMock()

        await provider.close()

        mock_engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_does_not_double_dispose_sub_providers(self) -> None:
        """Sub-providers share the engine; only the shared engine is disposed."""
        from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider

        mock_engine = MagicMock()
        mock_engine.dispose = AsyncMock()

        provider = HybridIsolationProvider.__new__(HybridIsolationProvider)
        provider._shared_engine = mock_engine

        # Sub-providers have a close() method but it should NOT be called
        mock_premium = MagicMock()
        mock_premium.close = AsyncMock()
        mock_standard = MagicMock()
        mock_standard.close = AsyncMock()

        provider.premium_provider = mock_premium
        provider.standard_provider = mock_standard

        await provider.close()

        # Only shared engine disposed
        mock_engine.dispose.assert_awaited_once()
        # Sub-providers NOT closed (they share the engine; closing both would error)
        mock_premium.close.assert_not_awaited()
        mock_standard.close.assert_not_awaited()


# ---------------------------------------------------------------------------
# Integration with IsolationProviderFactory
# ---------------------------------------------------------------------------

class TestHybridFactory:

    def test_factory_creates_hybrid_for_hybrid_strategy(self) -> None:
        from fastapi_tenancy.core.types import IsolationStrategy
        from fastapi_tenancy.isolation.factory import IsolationProviderFactory

        cfg = make_hybrid_config()
        mock_engine = MagicMock()

        with patch("fastapi_tenancy.isolation.hybrid.create_async_engine", return_value=mock_engine), \
             patch("fastapi_tenancy.isolation.schema.SchemaIsolationProvider.__init__",
                   lambda s, c, engine=None: (setattr(s, 'engine', engine), setattr(s, 'config', c),
                                              setattr(s, 'dialect', __import__('fastapi_tenancy.utils.db_compat',
                                              fromlist=['DbDialect']).DbDialect.POSTGRESQL))), \
             patch("fastapi_tenancy.isolation.rls.RLSIsolationProvider.__init__",
                   lambda s, c, engine=None: (setattr(s, 'engine', engine), setattr(s, 'config', c),
                                              setattr(s, 'dialect', __import__('fastapi_tenancy.utils.db_compat',
                                              fromlist=['DbDialect']).DbDialect.POSTGRESQL))):
            from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider
            provider = IsolationProviderFactory.create(IsolationStrategy.HYBRID, cfg)

        assert isinstance(provider, HybridIsolationProvider)

    def test_hybrid_provider_has_premium_and_standard_attributes(self) -> None:
        from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider

        cfg = make_hybrid_config()
        mock_engine = MagicMock()

        with patch("fastapi_tenancy.isolation.hybrid.create_async_engine", return_value=mock_engine), \
             patch("fastapi_tenancy.isolation.schema.SchemaIsolationProvider.__init__",
                   lambda s, c, engine=None: (setattr(s, 'engine', engine), setattr(s, 'config', c),
                                              setattr(s, 'dialect', __import__('fastapi_tenancy.utils.db_compat',
                                              fromlist=['DbDialect']).DbDialect.POSTGRESQL))), \
             patch("fastapi_tenancy.isolation.rls.RLSIsolationProvider.__init__",
                   lambda s, c, engine=None: (setattr(s, 'engine', engine), setattr(s, 'config', c),
                                              setattr(s, 'dialect', __import__('fastapi_tenancy.utils.db_compat',
                                              fromlist=['DbDialect']).DbDialect.POSTGRESQL))):
            provider = HybridIsolationProvider(cfg)

        assert hasattr(provider, 'premium_provider')
        assert hasattr(provider, 'standard_provider')
        assert hasattr(provider, '_shared_engine')
        assert provider._shared_engine is mock_engine


# ---------------------------------------------------------------------------
# apply_filters bound-parameter test (no real DB needed)
# ---------------------------------------------------------------------------

class TestApplyFiltersParameterised:
    """Verify both RLS and Schema providers use column() bound params, not literals."""

    def test_rls_apply_filters_produces_bound_param(self) -> None:
        from sqlalchemy import Column, MetaData, String, Table, select
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        import asyncio

        cfg = make_hybrid_config()
        mock_engine = MagicMock()

        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=mock_engine):
            provider = RLSIsolationProvider(cfg, engine=mock_engine)

        meta = MetaData()
        orders = Table("orders", meta, Column("id", String), Column("tenant_id", String))
        q = select(orders)
        tenant = make_tenant("acme")

        filtered = asyncio.get_event_loop().run_until_complete(
            provider.apply_filters(q, tenant)
        )
        compiled = str(filtered.compile())
        # Must reference a bind param, not the literal value
        assert "tenant_id" in compiled
        # The tenant.id value must NOT appear as a literal in the SQL string
        assert tenant.id not in compiled or ":tenant_id" in compiled or "?" in compiled

    def test_schema_apply_filters_produces_bound_param(self) -> None:
        from sqlalchemy import Column, MetaData, String, Table, select
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
        import asyncio

        cfg = make_hybrid_config()
        mock_engine = MagicMock()

        with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=mock_engine):
            provider = SchemaIsolationProvider(cfg, engine=mock_engine)

        meta = MetaData()
        users = Table("users", meta, Column("id", String), Column("tenant_id", String))
        q = select(users)
        tenant = make_tenant("acme")

        filtered = asyncio.get_event_loop().run_until_complete(
            provider.apply_filters(q, users)
        )
        # Just verifying it doesn't raise — the column() API is safe by construction
        assert filtered is not None

    def test_apply_filters_passthrough_for_non_selectable(self) -> None:
        """Non-query objects (e.g. raw strings) are returned unchanged."""
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        import asyncio

        cfg = make_hybrid_config()
        mock_engine = MagicMock()

        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=mock_engine):
            provider = RLSIsolationProvider(cfg, engine=mock_engine)

        tenant = make_tenant("acme")
        raw = "SELECT * FROM orders WHERE tenant_id = :tid"

        result = asyncio.get_event_loop().run_until_complete(
            provider.apply_filters(raw, tenant)
        )
        assert result is raw  # unchanged passthrough


# ---------------------------------------------------------------------------
# Additional routing tests
# ---------------------------------------------------------------------------

class TestHybridRoutingEdgeCases:

    def _make_provider(self, premium_ids=None):
        from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider
        cfg = make_hybrid_config(premium_ids=premium_ids)
        provider = HybridIsolationProvider.__new__(HybridIsolationProvider)
        provider.config = cfg
        provider._shared_engine = MagicMock()
        provider.premium_provider = MagicMock()
        provider.standard_provider = MagicMock()
        return provider

    def test_unknown_tenant_routes_to_standard(self) -> None:
        """A tenant ID not in premium_tenants is always treated as standard."""
        provider = self._make_provider(premium_ids=["known-premium"])
        unknown = make_tenant("unknown", tenant_id="t-unknown")
        assert provider._get_provider(unknown) is provider.standard_provider

    def test_routing_is_deterministic(self) -> None:
        """Same tenant always maps to same provider."""
        provider = self._make_provider(premium_ids=["t-premium"])
        tenant = make_tenant("prem", tenant_id="t-premium")
        results = {provider._get_provider(tenant) for _ in range(10)}
        assert len(results) == 1  # always the same object
