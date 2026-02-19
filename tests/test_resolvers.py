"""Unit tests for tenant resolvers — uses mock requests (no real HTTP server)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fastapi_tenancy.core.exceptions import TenantNotFoundError, TenantResolutionError
from fastapi_tenancy.core.types import Tenant
from fastapi_tenancy.resolution.header import HeaderTenantResolver
from fastapi_tenancy.storage.memory import InMemoryTenantStore


def make_request(headers: dict[str, str]) -> MagicMock:
    """Create a minimal mock request with the given headers."""
    req = MagicMock()
    req.headers = headers
    return req


@pytest.fixture
def store() -> InMemoryTenantStore:
    return InMemoryTenantStore()


@pytest.fixture
def acme_tenant() -> Tenant:
    return Tenant(id="acme-001", identifier="acme-corp", name="Acme Corp")


@pytest.fixture
def resolver(store: InMemoryTenantStore) -> HeaderTenantResolver:
    return HeaderTenantResolver(header_name="X-Tenant-ID", tenant_store=store)


class TestHeaderResolverResolve:

    @pytest.mark.asyncio
    async def test_resolves_valid_header(
        self,
        resolver: HeaderTenantResolver,
        store: InMemoryTenantStore,
        acme_tenant: Tenant,
    ) -> None:
        await store.create(acme_tenant)
        request = make_request({"X-Tenant-ID": "acme-corp"})
        tenant = await resolver.resolve(request)
        assert tenant.identifier == "acme-corp"

    @pytest.mark.asyncio
    async def test_missing_header_raises(self, resolver: HeaderTenantResolver) -> None:
        request = make_request({})
        with pytest.raises(TenantResolutionError, match="not found"):
            await resolver.resolve(request)

    @pytest.mark.asyncio
    async def test_empty_header_raises(self, resolver: HeaderTenantResolver) -> None:
        request = make_request({"X-Tenant-ID": "   "})
        with pytest.raises(TenantResolutionError):
            await resolver.resolve(request)

    @pytest.mark.asyncio
    async def test_invalid_format_raises(self, resolver: HeaderTenantResolver) -> None:
        # Uppercase is invalid per validate_tenant_identifier
        request = make_request({"X-Tenant-ID": "'; DROP TABLE tenants"})
        with pytest.raises(TenantResolutionError, match="Invalid"):
            await resolver.resolve(request)

    @pytest.mark.asyncio
    async def test_unknown_tenant_raises(
        self,
        resolver: HeaderTenantResolver,
    ) -> None:
        request = make_request({"X-Tenant-ID": "no-such-tenant"})
        with pytest.raises(TenantNotFoundError):
            await resolver.resolve(request)

    @pytest.mark.asyncio
    async def test_case_insensitive_header_name(
        self,
        store: InMemoryTenantStore,
        acme_tenant: Tenant,
    ) -> None:
        await store.create(acme_tenant)
        resolver = HeaderTenantResolver(header_name="X-Tenant-ID", tenant_store=store)
        # Starlette normalises header names to lowercase internally;
        # our resolver should still find it
        request = make_request({"x-tenant-id": "acme-corp"})
        tenant = await resolver.resolve(request)
        assert tenant.identifier == "acme-corp"

    @pytest.mark.asyncio
    async def test_whitespace_trimmed(
        self,
        store: InMemoryTenantStore,
        acme_tenant: Tenant,
    ) -> None:
        await store.create(acme_tenant)
        request = make_request({"X-Tenant-ID": "  acme-corp  "})
        tenant = await resolver.resolve(request) if False else None
        # Use a fresh resolver directly
        r = HeaderTenantResolver(tenant_store=store)
        t = await r.resolve(make_request({"X-Tenant-ID": "  acme-corp  "}))
        assert t.identifier == "acme-corp"
