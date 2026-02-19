"""ResolverFactory and all resolver classes — coverage for resolution/factory.py."""
from __future__ import annotations

import pytest

from fastapi_tenancy.core.types import ResolutionStrategy, Tenant, TenantStatus
from fastapi_tenancy.storage.memory import InMemoryTenantStore


def _store_with(*tenants: Tenant) -> InMemoryTenantStore:
    import asyncio
    store = InMemoryTenantStore()
    for t in tenants:
        asyncio.get_event_loop().run_until_complete(store.create(t))
    return store


def _make_config(**kwargs):
    from fastapi_tenancy.core.config import TenancyConfig
    defaults = dict(
        database_url="sqlite+aiosqlite:///:memory:",
        resolution_strategy="header",
        isolation_strategy="rls",
    )
    defaults.update(kwargs)
    return TenancyConfig(**defaults)


class TestResolverFactory:

    def test_creates_header_resolver(self) -> None:
        from fastapi_tenancy.resolution.factory import ResolverFactory
        from fastapi_tenancy.resolution.header import HeaderTenantResolver
        store = InMemoryTenantStore()
        r = ResolverFactory.create(ResolutionStrategy.HEADER, _make_config(), store)
        assert isinstance(r, HeaderTenantResolver)

    def test_creates_path_resolver(self) -> None:
        from fastapi_tenancy.resolution.factory import ResolverFactory
        from fastapi_tenancy.resolution.path import PathTenantResolver
        store = InMemoryTenantStore()
        r = ResolverFactory.create(ResolutionStrategy.PATH, _make_config(), store)
        assert isinstance(r, PathTenantResolver)

    def test_creates_subdomain_resolver(self) -> None:
        from fastapi_tenancy.resolution.factory import ResolverFactory
        from fastapi_tenancy.resolution.subdomain import SubdomainTenantResolver
        store = InMemoryTenantStore()
        cfg = _make_config(resolution_strategy="subdomain", domain_suffix="example.com")
        r = ResolverFactory.create(ResolutionStrategy.SUBDOMAIN, cfg, store)
        assert isinstance(r, SubdomainTenantResolver)

    def test_creates_jwt_resolver(self) -> None:
        from fastapi_tenancy.resolution.factory import ResolverFactory
        from fastapi_tenancy.resolution.jwt import JWTTenantResolver
        store = InMemoryTenantStore()
        cfg = _make_config(jwt_secret="my-secret")
        r = ResolverFactory.create(ResolutionStrategy.JWT, cfg, store)
        assert isinstance(r, JWTTenantResolver)

    def test_unknown_strategy_raises(self) -> None:
        from fastapi_tenancy.resolution.factory import ResolverFactory
        store = InMemoryTenantStore()
        with pytest.raises((ValueError, KeyError)):
            ResolverFactory.create("unknown", _make_config(), store)  # type: ignore


class TestStoreTenantStoreMethods:
    """TenantStore abstract base — get_by_ids, search, bulk_update_status."""

    @pytest.mark.asyncio
    async def test_get_by_ids(self) -> None:
        store = InMemoryTenantStore()
        t1 = Tenant(id="a", identifier="tenant-a", name="A")
        t2 = Tenant(id="b", identifier="tenant-b", name="B")
        await store.create(t1)
        await store.create(t2)
        results = await store.get_by_ids(["a", "b", "nonexistent"])
        assert len(results) == 2
        ids = {t.id for t in results}
        assert ids == {"a", "b"}

    @pytest.mark.asyncio
    async def test_search_by_name(self) -> None:
        store = InMemoryTenantStore()
        await store.create(Tenant(id="1", identifier="acme-corp", name="Acme Corporation"))
        await store.create(Tenant(id="2", identifier="globex", name="Globex LLC"))
        results = await store.search("acme")
        assert len(results) == 1
        assert results[0].identifier == "acme-corp"

    @pytest.mark.asyncio
    async def test_search_by_identifier(self) -> None:
        store = InMemoryTenantStore()
        await store.create(Tenant(id="1", identifier="acme-corp", name="Acme"))
        await store.create(Tenant(id="2", identifier="globex", name="Globex"))
        results = await store.search("globex")
        assert len(results) == 1
        assert results[0].identifier == "globex"

    @pytest.mark.asyncio
    async def test_search_returns_empty(self) -> None:
        store = InMemoryTenantStore()
        results = await store.search("no-match")
        assert results == []

    @pytest.mark.asyncio
    async def test_bulk_update_status(self) -> None:
        store = InMemoryTenantStore()
        await store.create(Tenant(id="a", identifier="a", name="A"))
        await store.create(Tenant(id="b", identifier="b", name="B"))
        results = await store.bulk_update_status(["a", "b", "ghost"], TenantStatus.SUSPENDED)
        assert len(results) == 2
        assert all(t.status == TenantStatus.SUSPENDED for t in results)

    @pytest.mark.asyncio
    async def test_bulk_update_status_skips_missing(self) -> None:
        store = InMemoryTenantStore()
        await store.create(Tenant(id="a", identifier="a", name="A"))
        results = await store.bulk_update_status(["a", "ghost"], TenantStatus.ACTIVE)
        assert len(results) == 1  # ghost silently skipped
