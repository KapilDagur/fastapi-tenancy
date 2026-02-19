"""Integration tests for SQLAlchemyTenantStore backed by SQLite (no PG needed).

Uses sqlite+aiosqlite in-memory with StaticPool for fast, hermetic testing.
"""
from __future__ import annotations

import pytest

from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.storage.postgres import SQLAlchemyTenantStore


@pytest.fixture
async def store():
    s = SQLAlchemyTenantStore(
        database_url="sqlite+aiosqlite:///:memory:",
        pool_size=1,
    )
    await s.initialize()
    yield s
    await s.close()


def make_tenant(suffix: str = "01") -> Tenant:
    return Tenant(
        id=f"t-{suffix}",
        identifier=f"acme-{suffix}",
        name=f"Acme {suffix}",
    )


class TestSQLiteStoreCRUD:

    @pytest.mark.asyncio
    async def test_create_and_get_by_id(self, store: SQLAlchemyTenantStore) -> None:
        t = make_tenant()
        created = await store.create(t)
        assert created.id == t.id
        fetched = await store.get_by_id(t.id)
        assert fetched.identifier == t.identifier

    @pytest.mark.asyncio
    async def test_get_by_identifier(self, store: SQLAlchemyTenantStore) -> None:
        t = make_tenant("ident")
        await store.create(t)
        fetched = await store.get_by_identifier(t.identifier)
        assert fetched.id == t.id

    @pytest.mark.asyncio
    async def test_get_missing_raises(self, store: SQLAlchemyTenantStore) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.get_by_id("ghost")

    @pytest.mark.asyncio
    async def test_duplicate_id_raises(self, store: SQLAlchemyTenantStore) -> None:
        t = make_tenant("dup")
        await store.create(t)
        with pytest.raises(ValueError):
            await store.create(t)

    @pytest.mark.asyncio
    async def test_update(self, store: SQLAlchemyTenantStore) -> None:
        t = make_tenant("upd")
        await store.create(t)
        updated = t.model_copy(update={"name": "Updated Name"})
        result = await store.update(updated)
        assert result.name == "Updated Name"

    @pytest.mark.asyncio
    async def test_delete(self, store: SQLAlchemyTenantStore) -> None:
        t = make_tenant("del")
        await store.create(t)
        await store.delete(t.id)
        with pytest.raises(TenantNotFoundError):
            await store.get_by_id(t.id)

    @pytest.mark.asyncio
    async def test_list_all(self, store: SQLAlchemyTenantStore) -> None:
        for i in range(3):
            await store.create(make_tenant(str(i)))
        items = await store.list()
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_list_filter_status(self, store: SQLAlchemyTenantStore) -> None:
        t_active = make_tenant("active")
        t_susp = make_tenant("susp")
        await store.create(t_active)
        created_susp = await store.create(t_susp)
        await store.set_status(created_susp.id, TenantStatus.SUSPENDED)
        active_list = await store.list(status=TenantStatus.ACTIVE)
        assert len(active_list) == 1

    @pytest.mark.asyncio
    async def test_count(self, store: SQLAlchemyTenantStore) -> None:
        assert await store.count() == 0
        await store.create(make_tenant("c1"))
        await store.create(make_tenant("c2"))
        assert await store.count() == 2

    @pytest.mark.asyncio
    async def test_exists(self, store: SQLAlchemyTenantStore) -> None:
        t = make_tenant("ex")
        assert await store.exists(t.id) is False
        await store.create(t)
        assert await store.exists(t.id) is True

    @pytest.mark.asyncio
    async def test_set_status(self, store: SQLAlchemyTenantStore) -> None:
        t = make_tenant("st")
        await store.create(t)
        result = await store.set_status(t.id, TenantStatus.SUSPENDED)
        assert result.status == TenantStatus.SUSPENDED

    @pytest.mark.asyncio
    async def test_update_metadata(self, store: SQLAlchemyTenantStore) -> None:
        t = make_tenant("meta")
        await store.create(t)
        result = await store.update_metadata(t.id, {"plan": "enterprise"})
        assert result.metadata["plan"] == "enterprise"

    @pytest.mark.asyncio
    async def test_metadata_json_roundtrip(self, store: SQLAlchemyTenantStore) -> None:
        t = Tenant(
            id="meta-rt",
            identifier="meta-rt",
            name="Meta RT",
            metadata={"nested": {"deep": True}, "count": 42},
        )
        await store.create(t)
        fetched = await store.get_by_id(t.id)
        assert fetched.metadata == t.metadata

    @pytest.mark.asyncio
    async def test_pagination(self, store: SQLAlchemyTenantStore) -> None:
        for i in range(5):
            await store.create(make_tenant(f"p{i}"))
        page1 = await store.list(skip=0, limit=3)
        page2 = await store.list(skip=3, limit=3)
        assert len(page1) == 3
        assert len(page2) == 2

    @pytest.mark.asyncio
    async def test_set_status_missing_raises(self, store: SQLAlchemyTenantStore) -> None:
        with pytest.raises(TenantNotFoundError):
            await store.set_status("ghost", TenantStatus.SUSPENDED)

    @pytest.mark.asyncio
    async def test_update_missing_raises(self, store: SQLAlchemyTenantStore) -> None:
        t = make_tenant("missing")
        with pytest.raises(TenantNotFoundError):
            await store.update(t)
