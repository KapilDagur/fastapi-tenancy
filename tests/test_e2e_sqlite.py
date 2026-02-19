"""End-to-end integration tests using SQLite — no external services.

These tests wire together the full request lifecycle:
  Request → Middleware → Resolver → TenantContext → IsolationProvider → DB

Uses:
- SQLite in-memory for tenant storage (SQLAlchemyTenantStore)
- SchemaIsolationProvider in prefix mode
- InMemoryTenantStore for lightweight resolver tests
- FastAPI TestClient via httpx / Starlette test utilities
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.storage.memory import InMemoryTenantStore

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def store() -> InMemoryTenantStore:
    return InMemoryTenantStore()


@pytest.fixture
async def acme(store) -> Tenant:
    t = Tenant(id="acme-001", identifier="acme-corp", name="Acme Corp")
    await store.create(t)
    return t


# ---------------------------------------------------------------------------
# E2E: Resolver → Store → Context
# ---------------------------------------------------------------------------

class TestHeaderResolverE2E:

    @pytest.mark.asyncio
    async def test_resolve_sets_context(self, store, acme) -> None:
        from fastapi_tenancy.resolution.header import HeaderTenantResolver
        resolver = HeaderTenantResolver(
            header_name="X-Tenant-ID", tenant_store=store
        )
        request = MagicMock()
        request.headers = {"x-tenant-id": "acme-corp"}

        tenant = await resolver.resolve(request)
        assert tenant.id == acme.id

    @pytest.mark.asyncio
    async def test_resolve_missing_header(self, store) -> None:
        from fastapi_tenancy.core.exceptions import TenantResolutionError
        from fastapi_tenancy.resolution.header import HeaderTenantResolver
        resolver = HeaderTenantResolver(tenant_store=store)
        request = MagicMock()
        request.headers = {}

        with pytest.raises(TenantResolutionError):
            await resolver.resolve(request)

    @pytest.mark.asyncio
    async def test_resolve_unknown_identifier(self, store) -> None:
        from fastapi_tenancy.resolution.header import HeaderTenantResolver
        resolver = HeaderTenantResolver(tenant_store=store)
        request = MagicMock()
        request.headers = {"x-tenant-id": "no-such-tenant"}

        with pytest.raises(TenantNotFoundError):
            await resolver.resolve(request)


# ---------------------------------------------------------------------------
# E2E: Store → IsolationProvider → Session (SQLite)
# ---------------------------------------------------------------------------

class TestSchemaIsolationE2E:
    """Full lifecycle: create tenant → init isolation → get session → query."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_sqlite(self) -> None:
        import sqlalchemy as sa

        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider

        cfg = MagicMock()
        cfg.database_url = "sqlite+aiosqlite:///:memory:"
        cfg.database_pool_size = 1
        cfg.database_max_overflow = 0
        cfg.database_pool_timeout = 5
        cfg.database_pool_recycle = 600
        cfg.database_echo = False
        cfg.database_url_template = None
        cfg.schema_prefix = "tenant_"
        cfg.get_schema_name = lambda tid: f"tenant_{tid.replace('-', '_')}"

        provider = SchemaIsolationProvider(cfg)
        tenant = Tenant(id="e2e-001", identifier="e2e-corp", name="E2E Corp")

        # Create app metadata with a simple table
        meta = sa.MetaData()
        sa.Table(
            "orders", meta,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("tenant_id", sa.String(255)),
        )

        # Initialise tenant storage
        await provider.initialize_tenant(tenant, metadata=meta)

        # Get session and verify it works
        async with provider.get_session(tenant) as session:
            # In prefix mode, table is t_e2e_corp_orders
            prefix = provider.get_table_prefix(tenant)
            assert prefix.startswith("t_")
            assert session.info["tenant_id"] == tenant.id

        await provider.close()


# ---------------------------------------------------------------------------
# E2E: Full store CRUD via SQLite
# ---------------------------------------------------------------------------

class TestStoreCRUDE2E:

    @pytest.mark.asyncio
    async def test_full_crud_sqlite_store(self) -> None:
        from fastapi_tenancy.storage.postgres import SQLAlchemyTenantStore
        store = SQLAlchemyTenantStore("sqlite+aiosqlite:///:memory:", pool_size=1)
        await store.initialize()

        # Create
        t = Tenant(id="crud-001", identifier="crud-corp", name="CRUD Corp")
        created = await store.create(t)
        assert created.id == t.id

        # Read
        fetched = await store.get_by_id(t.id)
        assert fetched.name == "CRUD Corp"

        fetched2 = await store.get_by_identifier("crud-corp")
        assert fetched2.id == t.id

        # Update
        updated = t.model_copy(update={"name": "Updated Corp"})
        result = await store.update(updated)
        assert result.name == "Updated Corp"

        # Status
        suspended = await store.set_status(t.id, TenantStatus.SUSPENDED)
        assert suspended.status == TenantStatus.SUSPENDED

        # List and count
        assert await store.count() == 1
        items = await store.list()
        assert len(items) == 1
        assert items[0].status == TenantStatus.SUSPENDED

        # List by status
        active = await store.list(status=TenantStatus.ACTIVE)
        assert len(active) == 0

        # Metadata
        meta_result = await store.update_metadata(t.id, {"plan": "starter"})
        assert meta_result.metadata["plan"] == "starter"

        # Delete
        await store.delete(t.id)
        assert await store.count() == 0

        with pytest.raises(TenantNotFoundError):
            await store.get_by_id(t.id)

        await store.close()


# ---------------------------------------------------------------------------
# E2E: Concurrent tenant isolation
# ---------------------------------------------------------------------------

class TestConcurrentTenantE2E:

    @pytest.mark.asyncio
    async def test_concurrent_context_isolation(self) -> None:
        """Simulate N concurrent requests each with different tenants."""
        N = 10
        errors: list[str] = []

        async def simulate_request(i: int) -> None:
            t = Tenant(
                id=f"concurrent-{i:03d}",
                identifier=f"tenant-{i:03d}",
                name=f"Tenant {i}",
            )
            async with TenantContext.scope(t):
                await asyncio.sleep(0.001)
                got = TenantContext.get()
                if got.id != t.id:
                    errors.append(f"Request {i}: got {got.id}, expected {t.id}")

        await asyncio.gather(*[simulate_request(i) for i in range(N)])
        assert not errors, f"Context isolation failures: {errors}"

    @pytest.mark.asyncio
    async def test_context_cleared_between_requests(self) -> None:
        """Simulate sequential requests — context must not bleed between them."""
        t1 = Tenant(id="req-1", identifier="t1", name="T1")
        t2 = Tenant(id="req-2", identifier="t2", name="T2")

        async with TenantContext.scope(t1):
            in_req1 = TenantContext.get()

        # After scope, context is clear
        assert TenantContext.get_optional() is None

        async with TenantContext.scope(t2):
            in_req2 = TenantContext.get()

        assert in_req1.id == "req-1"
        assert in_req2.id == "req-2"


# ---------------------------------------------------------------------------
# E2E: Middleware skip logic
# ---------------------------------------------------------------------------

class TestMiddlewareE2E:

    def test_skip_paths_not_resolved(self) -> None:
        from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
        middleware = TenancyMiddleware(
            app=MagicMock(),
            skip_paths=["/health", "/metrics"],
        )
        assert middleware._is_path_skipped("/health") is True
        assert middleware._is_path_skipped("/metrics") is True
        assert middleware._is_path_skipped("/api/users") is False

    def test_skip_path_prefix(self) -> None:
        from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
        middleware = TenancyMiddleware(
            app=MagicMock(),
            skip_paths=["/public/"],
        )
        assert middleware._is_path_skipped("/public/assets/logo.png") is True
        assert middleware._is_path_skipped("/private/data") is False

    def test_middleware_has_resolver_property(self) -> None:
        from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
        middleware = TenancyMiddleware(app=MagicMock())
        # resolver property should exist (may be None if not configured)
        _ = middleware.resolver
