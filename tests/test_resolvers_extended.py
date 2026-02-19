"""Extended resolver tests — path, subdomain, JWT; edge cases."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fastapi_tenancy.core.exceptions import TenantNotFoundError, TenantResolutionError
from fastapi_tenancy.core.types import Tenant
from fastapi_tenancy.resolution.path import PathTenantResolver
from fastapi_tenancy.resolution.subdomain import SubdomainTenantResolver
from fastapi_tenancy.storage.memory import InMemoryTenantStore


def req(path: str = "/", host: str = "localhost", headers: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.url.path = path
    r.headers = headers or {}
    r.url.hostname = host
    return r


@pytest.fixture
def store() -> InMemoryTenantStore:
    return InMemoryTenantStore()


@pytest.fixture
def acme() -> Tenant:
    return Tenant(id="acme-001", identifier="acme-corp", name="Acme Corp")


class TestPathResolver:

    @pytest.fixture
    def resolver(self, store: InMemoryTenantStore) -> PathTenantResolver:
        return PathTenantResolver(path_prefix="/tenants", tenant_store=store)

    @pytest.mark.asyncio
    async def test_resolves_from_path(
        self, resolver: PathTenantResolver, store: InMemoryTenantStore, acme: Tenant
    ) -> None:
        await store.create(acme)
        request = req(path="/tenants/acme-corp/dashboard")
        tenant = await resolver.resolve(request)
        assert tenant.identifier == "acme-corp"

    @pytest.mark.asyncio
    async def test_wrong_prefix_raises(self, resolver: PathTenantResolver) -> None:
        with pytest.raises(TenantResolutionError):
            await resolver.resolve(req(path="/api/users"))

    @pytest.mark.asyncio
    async def test_missing_segment_raises(self, resolver: PathTenantResolver) -> None:
        with pytest.raises(TenantResolutionError):
            await resolver.resolve(req(path="/tenants"))

    @pytest.mark.asyncio
    async def test_unknown_tenant_raises(self, resolver: PathTenantResolver) -> None:
        with pytest.raises(TenantNotFoundError):
            await resolver.resolve(req(path="/tenants/no-such-tenant/api"))

    @pytest.mark.asyncio
    async def test_invalid_identifier_raises(self, resolver: PathTenantResolver) -> None:
        with pytest.raises(TenantResolutionError):
            await resolver.resolve(req(path="/tenants/'; DROP TABLE/api"))


class TestSubdomainResolver:

    @pytest.fixture
    def resolver(self, store: InMemoryTenantStore) -> SubdomainTenantResolver:
        return SubdomainTenantResolver(domain_suffix=".example.com", tenant_store=store)

    @pytest.mark.asyncio
    async def test_resolves_from_subdomain(
        self, resolver: SubdomainTenantResolver, store: InMemoryTenantStore, acme: Tenant
    ) -> None:
        await store.create(acme)
        request = req(host="acme-corp.example.com")
        tenant = await resolver.resolve(request)
        assert tenant.identifier == "acme-corp"

    @pytest.mark.asyncio
    async def test_apex_domain_raises(self, resolver: SubdomainTenantResolver) -> None:
        with pytest.raises(TenantResolutionError):
            await resolver.resolve(req(host="example.com"))

    @pytest.mark.asyncio
    async def test_wrong_domain_raises(self, resolver: SubdomainTenantResolver) -> None:
        with pytest.raises(TenantResolutionError):
            await resolver.resolve(req(host="acme.other.com"))

    @pytest.mark.asyncio
    async def test_unknown_subdomain_raises(self, resolver: SubdomainTenantResolver) -> None:
        with pytest.raises(TenantNotFoundError):
            await resolver.resolve(req(host="unknown.example.com"))


class TestBaseTenantResolverHelpers:

    def test_validate_known_good_identifiers(self) -> None:
        from fastapi_tenancy.utils.validation import validate_tenant_identifier
        # Access via the static helper
        for slug in ["acme-corp", "my-tenant", "tenant-01"]:
            assert validate_tenant_identifier(slug) is True

    def test_no_store_raises_on_lookup(self) -> None:
        from fastapi_tenancy.resolution.header import HeaderTenantResolver
        resolver = HeaderTenantResolver(tenant_store=None)

        import asyncio
        with pytest.raises(ValueError, match="tenant_store"):
            asyncio.get_event_loop().run_until_complete(
                resolver.get_tenant_by_identifier("acme-corp")
            )
