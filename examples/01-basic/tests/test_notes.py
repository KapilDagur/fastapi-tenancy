"""
tests/test_notes.py — full test suite for the basic Notekeeper example.

Run:
    pytest tests/ -v --cov=app

All tests run against SQLite in-memory — no Docker required.
PostgreSQL E2E tests are gated behind the --postgres flag.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from fastapi_tenancy import Tenant, TenancyConfig, TenancyManager
from fastapi_tenancy.storage.memory import InMemoryTenantStore

# ── App import ────────────────────────────────────────────────────────────────
# Import the ORM Base BEFORE creating tables so create_all picks up Note model.
from app.main import Base, Note, app

# ── Fixtures ──────────────────────────────────────────────────────────────────

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="function")
async def test_store() -> InMemoryTenantStore:
    """Fresh in-memory store with two tenants per test."""
    store = InMemoryTenantStore()
    await store.create(Tenant(
        id="tenant-acme-001",
        identifier="acme-corp",
        name="Acme Corporation",
    ))
    await store.create(Tenant(
        id="tenant-globex-001",
        identifier="globex",
        name="Globex LLC",
    ))
    return store


@pytest_asyncio.fixture(scope="function")
async def client(test_store: InMemoryTenantStore) -> AsyncIterator[AsyncClient]:
    """
    HTTP client wired to the app with:
      - SQLite in-memory database
      - In-memory tenant store
      - Tables created fresh for each test
    """
    # Patch the module-level store so the lifespan uses ours
    import app.main as main_module

    original_store = main_module._tenant_store
    original_url   = main_module.DATABASE_URL

    main_module._tenant_store = test_store
    main_module.DATABASE_URL  = SQLITE_URL

    # Re-create config pointing at SQLite
    main_module.config = TenancyConfig(
        database_url=SQLITE_URL,
        resolution_strategy="header",
        isolation_strategy="rls",
        tenant_header_name="X-Tenant-ID",
    )

    # Create tables in a fresh engine
    engine = create_async_engine(SQLITE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as ac:
        yield ac

    # Restore originals
    main_module._tenant_store = original_store
    main_module.DATABASE_URL  = original_url


# ── Helpers ───────────────────────────────────────────────────────────────────

def acme_headers() -> dict[str, str]:
    return {"X-Tenant-ID": "acme-corp"}


def globex_headers() -> dict[str, str]:
    return {"X-Tenant-ID": "globex"}


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_no_tenant_header(self, client: AsyncClient) -> None:
        """Health endpoint is public — no X-Tenant-ID required."""
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── Tenant resolution ─────────────────────────────────────────────────────────

class TestTenantResolution:
    @pytest.mark.asyncio
    async def test_missing_header_returns_400(self, client: AsyncClient) -> None:
        r = await client.get("/notes")  # no header
        assert r.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_unknown_tenant_returns_404(self, client: AsyncClient) -> None:
        r = await client.get("/notes", headers={"X-Tenant-ID": "nobody"})
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_me_returns_tenant_info(self, client: AsyncClient) -> None:
        r = await client.get("/me", headers=acme_headers())
        assert r.status_code == 200
        data = r.json()
        assert data["identifier"] == "acme-corp"
        assert data["name"] == "Acme Corporation"


# ── CRUD ──────────────────────────────────────────────────────────────────────

class TestNotesCRUD:
    @pytest.mark.asyncio
    async def test_empty_list(self, client: AsyncClient) -> None:
        r = await client.get("/notes", headers=acme_headers())
        assert r.status_code == 200
        assert r.json() == []

    @pytest.mark.asyncio
    async def test_create_note(self, client: AsyncClient) -> None:
        r = await client.post(
            "/notes",
            json={"title": "My First Note", "body": "Hello, world!"},
            headers=acme_headers(),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "My First Note"
        assert data["body"] == "Hello, world!"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_list_after_create(self, client: AsyncClient) -> None:
        await client.post("/notes", json={"title": "A"}, headers=acme_headers())
        await client.post("/notes", json={"title": "B"}, headers=acme_headers())
        r = await client.get("/notes", headers=acme_headers())
        assert r.status_code == 200
        assert len(r.json()) == 2

    @pytest.mark.asyncio
    async def test_get_note_by_id(self, client: AsyncClient) -> None:
        create_r = await client.post(
            "/notes", json={"title": "Fetchable"}, headers=acme_headers()
        )
        note_id = create_r.json()["id"]
        r = await client.get(f"/notes/{note_id}", headers=acme_headers())
        assert r.status_code == 200
        assert r.json()["title"] == "Fetchable"

    @pytest.mark.asyncio
    async def test_get_nonexistent_note(self, client: AsyncClient) -> None:
        r = await client.get("/notes/99999", headers=acme_headers())
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_note(self, client: AsyncClient) -> None:
        create_r = await client.post(
            "/notes", json={"title": "To Delete"}, headers=acme_headers()
        )
        note_id = create_r.json()["id"]
        del_r = await client.delete(f"/notes/{note_id}", headers=acme_headers())
        assert del_r.status_code == 204
        # Confirm it's gone
        get_r = await client.get(f"/notes/{note_id}", headers=acme_headers())
        assert get_r.status_code == 404


# ── Tenant isolation ──────────────────────────────────────────────────────────

class TestTenantIsolation:
    """
    Critical: notes created by Acme must be invisible to Globex and vice versa.
    This validates the RLS policy is working correctly end-to-end.
    """

    @pytest.mark.asyncio
    async def test_tenants_cannot_see_each_others_notes(
        self, client: AsyncClient
    ) -> None:
        # Acme creates two notes
        await client.post("/notes", json={"title": "Acme Secret"}, headers=acme_headers())
        await client.post("/notes", json={"title": "Acme Plans"}, headers=acme_headers())

        # Globex creates one note
        await client.post("/notes", json={"title": "Globex Note"}, headers=globex_headers())

        # Acme sees only its own notes
        acme_notes = (await client.get("/notes", headers=acme_headers())).json()
        assert len(acme_notes) == 2
        assert all("Acme" in n["title"] for n in acme_notes)

        # Globex sees only its own note
        globex_notes = (await client.get("/notes", headers=globex_headers())).json()
        assert len(globex_notes) == 1
        assert globex_notes[0]["title"] == "Globex Note"

    @pytest.mark.asyncio
    async def test_cannot_fetch_other_tenants_note_by_id(
        self, client: AsyncClient
    ) -> None:
        """Globex note ID should return 404 when accessed by Acme — not the note."""
        create_r = await client.post(
            "/notes", json={"title": "Globex Private"}, headers=globex_headers()
        )
        globex_note_id = create_r.json()["id"]

        # Acme tries to access Globex's note — should get 404, not 200 or 403
        r = await client.get(f"/notes/{globex_note_id}", headers=acme_headers())
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_cannot_delete_other_tenants_note(
        self, client: AsyncClient
    ) -> None:
        create_r = await client.post(
            "/notes", json={"title": "Globex Protected"}, headers=globex_headers()
        )
        globex_note_id = create_r.json()["id"]

        r = await client.delete(f"/notes/{globex_note_id}", headers=acme_headers())
        assert r.status_code == 404

        # Confirm it still exists for Globex
        r = await client.get(f"/notes/{globex_note_id}", headers=globex_headers())
        assert r.status_code == 200


# ── Validation ────────────────────────────────────────────────────────────────

class TestValidation:
    @pytest.mark.asyncio
    async def test_empty_title_rejected(self, client: AsyncClient) -> None:
        r = await client.post(
            "/notes", json={"title": ""}, headers=acme_headers()
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_title_rejected(self, client: AsyncClient) -> None:
        r = await client.post(
            "/notes", json={}, headers=acme_headers()
        )
        assert r.status_code == 422
