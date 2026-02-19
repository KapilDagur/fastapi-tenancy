"""Extra coverage for storage/memory.py and storage/postgres.py uncovered lines."""
from __future__ import annotations

import pytest

from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.storage.memory import InMemoryTenantStore


class TestMemoryStoreEdgeCases:
    """Hit the uncovered lines in memory.py (155-157, 322-328, etc.)."""

    @pytest.mark.asyncio
    async def test_create_duplicate_id_raises(self) -> None:
        store = InMemoryTenantStore()
        t = Tenant(id="dup-id", identifier="dup-corp", name="Dup")
        await store.create(t)
        with pytest.raises((ValueError, Exception)):
            await store.create(t)

    @pytest.mark.asyncio
    async def test_create_duplicate_identifier_raises(self) -> None:
        store = InMemoryTenantStore()
        t1 = Tenant(id="id-1", identifier="same-slug", name="One")
        t2 = Tenant(id="id-2", identifier="same-slug", name="Two")
        await store.create(t1)
        with pytest.raises((ValueError, Exception)):
            await store.create(t2)

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self) -> None:
        store = InMemoryTenantStore()
        t = Tenant(id="ghost", identifier="ghost", name="Ghost")
        with pytest.raises(TenantNotFoundError):
            await store.update(t)

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self) -> None:
        store = InMemoryTenantStore()
        with pytest.raises(TenantNotFoundError):
            await store.delete("ghost")

    @pytest.mark.asyncio
    async def test_set_status_nonexistent_raises(self) -> None:
        store = InMemoryTenantStore()
        with pytest.raises(TenantNotFoundError):
            await store.set_status("ghost", TenantStatus.ACTIVE)

    @pytest.mark.asyncio
    async def test_update_metadata_nonexistent_raises(self) -> None:
        store = InMemoryTenantStore()
        with pytest.raises(TenantNotFoundError):
            await store.update_metadata("ghost", {"key": "val"})

    @pytest.mark.asyncio
    async def test_list_all_statuses(self) -> None:
        store = InMemoryTenantStore()
        for i, status in enumerate(TenantStatus):
            await store.create(Tenant(
                id=f"t{i}", identifier=f"tenant-{i}", name=f"T{i}", status=status
            ))
        for status in TenantStatus:
            results = await store.list(status=status)
            assert all(t.status == status for t in results)

    @pytest.mark.asyncio
    async def test_list_with_limit(self) -> None:
        store = InMemoryTenantStore()
        for i in range(10):
            await store.create(Tenant(id=f"t{i}", identifier=f"ten-{i}", name=f"T{i}"))
        page = await store.list(skip=0, limit=3)
        assert len(page) == 3

    @pytest.mark.asyncio
    async def test_count_with_status_filter(self) -> None:
        store = InMemoryTenantStore()
        for i in range(3):
            await store.create(Tenant(id=f"t{i}", identifier=f"ten-{i}", name=f"T{i}"))
        await store.set_status("t0", TenantStatus.SUSPENDED)
        assert await store.count(status=TenantStatus.ACTIVE) == 2
        assert await store.count(status=TenantStatus.SUSPENDED) == 1

    @pytest.mark.asyncio
    async def test_exists_returns_false_for_missing(self) -> None:
        store = InMemoryTenantStore()
        assert await store.exists("no-such-id") is False

    @pytest.mark.asyncio
    async def test_full_round_trip(self) -> None:
        store = InMemoryTenantStore()
        t = Tenant(id="rt", identifier="round-trip", name="Round Trip",
                   metadata={"k": "v"})
        created = await store.create(t)
        assert created.id == "rt"
        fetched = await store.get_by_identifier("round-trip")
        assert fetched.metadata == {"k": "v"}
        updated = await store.update(t.model_copy(update={"name": "Updated"}))
        assert updated.name == "Updated"
        meta = await store.update_metadata("rt", {"new": "value"})
        assert meta.metadata["new"] == "value"
        assert meta.metadata["k"] == "v"  # merged, not replaced
        await store.delete("rt")
        with pytest.raises(TenantNotFoundError):
            await store.get_by_id("rt")


class TestPostgresStoreSQLite:
    """Hit uncovered lines in postgres.py using SQLite backend."""

    @pytest.fixture
    async def store(self):
        from fastapi_tenancy.storage.postgres import SQLAlchemyTenantStore
        s = SQLAlchemyTenantStore("sqlite+aiosqlite:///:memory:", pool_size=1)
        await s.initialize()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_create_and_get(self, store) -> None:
        t = Tenant(id="pg-1", identifier="pg-corp", name="PG Corp")
        created = await store.create(t)
        assert created.id == "pg-1"
        fetched = await store.get_by_id("pg-1")
        assert fetched.identifier == "pg-corp"

    @pytest.mark.asyncio
    async def test_get_missing_raises(self, store) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.get_by_id("ghost")

    @pytest.mark.asyncio
    async def test_get_by_identifier_missing_raises(self, store) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.get_by_identifier("ghost-identifier")

    @pytest.mark.asyncio
    async def test_update_metadata_merges(self, store) -> None:
        t = Tenant(id="m1", identifier="meta-corp", name="Meta", metadata={"a": 1})
        await store.create(t)
        updated = await store.update_metadata("m1", {"b": 2})
        assert updated.metadata["a"] == 1
        assert updated.metadata["b"] == 2

    @pytest.mark.asyncio
    async def test_set_status(self, store) -> None:
        t = Tenant(id="s1", identifier="status-corp", name="Status")
        await store.create(t)
        result = await store.set_status("s1", TenantStatus.SUSPENDED)
        assert result.status == TenantStatus.SUSPENDED

    @pytest.mark.asyncio
    async def test_exists_true_and_false(self, store) -> None:
        t = Tenant(id="e1", identifier="exists-corp", name="Exists")
        await store.create(t)
        assert await store.exists("e1") is True
        assert await store.exists("ghost") is False

    @pytest.mark.asyncio
    async def test_count(self, store) -> None:
        assert await store.count() == 0
        await store.create(Tenant(id="c1", identifier="c1", name="C1"))
        await store.create(Tenant(id="c2", identifier="c2", name="C2"))
        assert await store.count() == 2

    @pytest.mark.asyncio
    async def test_delete(self, store) -> None:
        t = Tenant(id="d1", identifier="delete-corp", name="Delete")
        await store.create(t)
        await store.delete("d1")
        with pytest.raises(TenantNotFoundError):
            await store.get_by_id("d1")

    @pytest.mark.asyncio
    async def test_list_paginated(self, store) -> None:
        for i in range(5):
            await store.create(Tenant(id=f"p{i}", identifier=f"pg-ten-{i}", name=f"P{i}"))
        page1 = await store.list(skip=0, limit=3)
        page2 = await store.list(skip=3, limit=3)
        assert len(page1) == 3
        assert len(page2) == 2

    @pytest.mark.asyncio
    async def test_update(self, store) -> None:
        t = Tenant(id="u1", identifier="update-corp", name="Old Name")
        await store.create(t)
        updated = await store.update(t.model_copy(update={"name": "New Name"}))
        assert updated.name == "New Name"

    @pytest.mark.asyncio
    async def test_update_missing_raises(self, store) -> None:
        t = Tenant(id="ghost", identifier="ghost", name="Ghost")
        with pytest.raises(TenantNotFoundError):
            await store.update(t)
