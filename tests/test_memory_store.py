"""Comprehensive InMemoryTenantStore tests — 100% coverage target."""
from __future__ import annotations

import pytest

from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.storage.memory import InMemoryTenantStore


def make_tenant(suffix: str = "01") -> Tenant:
    return Tenant(
        id=f"t-{suffix}",
        identifier=f"acme-{suffix}",
        name=f"Acme {suffix}",
    )


@pytest.fixture
def store() -> InMemoryTenantStore:
    return InMemoryTenantStore()


class TestCreate:

    @pytest.mark.asyncio
    async def test_create_returns_tenant(self, store: InMemoryTenantStore) -> None:
        t = make_tenant()
        created = await store.create(t)
        assert created.id == t.id
        assert created.identifier == t.identifier

    @pytest.mark.asyncio
    async def test_create_duplicate_id_raises(self, store: InMemoryTenantStore) -> None:
        t = make_tenant()
        await store.create(t)
        with pytest.raises((ValueError, Exception)):
            await store.create(t)

    @pytest.mark.asyncio
    async def test_create_duplicate_identifier_raises(self, store: InMemoryTenantStore) -> None:
        t1 = Tenant(id="unique-id", identifier="same-identifier", name="T1")
        t2 = Tenant(id="other-id", identifier="same-identifier", name="T2")
        await store.create(t1)
        with pytest.raises((ValueError, Exception)):
            await store.create(t2)

    @pytest.mark.asyncio
    async def test_create_multiple(self, store: InMemoryTenantStore) -> None:
        for i in range(5):
            await store.create(make_tenant(str(i)))
        assert await store.count() == 5


class TestRead:

    @pytest.mark.asyncio
    async def test_get_by_id(self, store: InMemoryTenantStore) -> None:
        t = make_tenant()
        await store.create(t)
        fetched = await store.get_by_id(t.id)
        assert fetched.id == t.id

    @pytest.mark.asyncio
    async def test_get_by_identifier(self, store: InMemoryTenantStore) -> None:
        t = make_tenant()
        await store.create(t)
        fetched = await store.get_by_identifier(t.identifier)
        assert fetched.id == t.id

    @pytest.mark.asyncio
    async def test_get_by_id_missing_raises(self, store: InMemoryTenantStore) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.get_by_id("ghost-id")

    @pytest.mark.asyncio
    async def test_get_by_identifier_missing_raises(self, store: InMemoryTenantStore) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.get_by_identifier("ghost-identifier")

    @pytest.mark.asyncio
    async def test_exists_true(self, store: InMemoryTenantStore) -> None:
        t = make_tenant()
        await store.create(t)
        assert await store.exists(t.id) is True

    @pytest.mark.asyncio
    async def test_exists_false(self, store: InMemoryTenantStore) -> None:
        assert await store.exists("nonexistent") is False


class TestUpdate:

    @pytest.mark.asyncio
    async def test_update_name(self, store: InMemoryTenantStore) -> None:
        t = make_tenant()
        await store.create(t)
        updated = t.model_copy(update={"name": "New Name"})
        result = await store.update(updated)
        assert result.name == "New Name"

    @pytest.mark.asyncio
    async def test_update_missing_raises(self, store: InMemoryTenantStore) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.update(make_tenant("ghost"))

    @pytest.mark.asyncio
    async def test_set_status_active_to_suspended(self, store: InMemoryTenantStore) -> None:
        t = make_tenant()
        await store.create(t)
        result = await store.set_status(t.id, TenantStatus.SUSPENDED)
        assert result.status == TenantStatus.SUSPENDED

    @pytest.mark.asyncio
    async def test_set_status_missing_raises(self, store: InMemoryTenantStore) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.set_status("ghost", TenantStatus.SUSPENDED)

    @pytest.mark.asyncio
    async def test_update_metadata_merges(self, store: InMemoryTenantStore) -> None:
        t = Tenant(id="meta-1", identifier="meta-1", name="M", metadata={"a": 1})
        await store.create(t)
        result = await store.update_metadata(t.id, {"b": 2})
        assert result.metadata["a"] == 1
        assert result.metadata["b"] == 2

    @pytest.mark.asyncio
    async def test_update_metadata_missing_raises(self, store: InMemoryTenantStore) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.update_metadata("ghost", {"k": "v"})


class TestDelete:

    @pytest.mark.asyncio
    async def test_delete_existing(self, store: InMemoryTenantStore) -> None:
        t = make_tenant()
        await store.create(t)
        await store.delete(t.id)
        assert await store.exists(t.id) is False

    @pytest.mark.asyncio
    async def test_delete_missing_raises(self, store: InMemoryTenantStore) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.delete("ghost")


class TestList:

    @pytest.mark.asyncio
    async def test_list_all(self, store: InMemoryTenantStore) -> None:
        for i in range(4):
            await store.create(make_tenant(str(i)))
        items = await store.list()
        assert len(items) == 4

    @pytest.mark.asyncio
    async def test_list_filter_active(self, store: InMemoryTenantStore) -> None:
        t1 = make_tenant("1")
        t2 = make_tenant("2")
        await store.create(t1)
        await store.create(t2)
        await store.set_status(t2.id, TenantStatus.SUSPENDED)
        active = await store.list(status=TenantStatus.ACTIVE)
        assert len(active) == 1
        assert active[0].id == t1.id

    @pytest.mark.asyncio
    async def test_list_filter_suspended(self, store: InMemoryTenantStore) -> None:
        for i in range(3):
            t = make_tenant(str(i))
            await store.create(t)
            if i > 0:
                await store.set_status(t.id, TenantStatus.SUSPENDED)
        suspended = await store.list(status=TenantStatus.SUSPENDED)
        assert len(suspended) == 2

    @pytest.mark.asyncio
    async def test_list_pagination(self, store: InMemoryTenantStore) -> None:
        for i in range(6):
            await store.create(make_tenant(str(i)))
        page1 = await store.list(skip=0, limit=3)
        page2 = await store.list(skip=3, limit=3)
        assert len(page1) == 3
        assert len(page2) == 3
        ids1 = {t.id for t in page1}
        ids2 = {t.id for t in page2}
        assert ids1.isdisjoint(ids2)

    @pytest.mark.asyncio
    async def test_list_empty(self, store: InMemoryTenantStore) -> None:
        items = await store.list()
        assert items == []

    @pytest.mark.asyncio
    async def test_count_total(self, store: InMemoryTenantStore) -> None:
        assert await store.count() == 0
        for i in range(3):
            await store.create(make_tenant(str(i)))
        assert await store.count() == 3

    @pytest.mark.asyncio
    async def test_count_by_status(self, store: InMemoryTenantStore) -> None:
        for i in range(4):
            t = make_tenant(str(i))
            await store.create(t)
            if i >= 2:
                await store.set_status(t.id, TenantStatus.SUSPENDED)
        assert await store.count(status=TenantStatus.ACTIVE) == 2
        assert await store.count(status=TenantStatus.SUSPENDED) == 2


class TestBulkOperations:

    @pytest.mark.asyncio
    async def test_bulk_create_and_list(self, store: InMemoryTenantStore) -> None:
        tenants = [make_tenant(str(i)) for i in range(10)]
        for t in tenants:
            await store.create(t)
        result = await store.list(skip=0, limit=100)
        assert len(result) == 10

    @pytest.mark.asyncio
    async def test_isolation_between_tenants(self, store: InMemoryTenantStore) -> None:
        """Updating one tenant must not affect others."""
        t1 = make_tenant("iso1")
        t2 = make_tenant("iso2")
        await store.create(t1)
        await store.create(t2)
        await store.update_metadata(t1.id, {"key": "for-t1"})
        t2_fetched = await store.get_by_id(t2.id)
        assert "key" not in t2_fetched.metadata
