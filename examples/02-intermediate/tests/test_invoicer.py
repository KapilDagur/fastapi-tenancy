"""
tests/test_invoicer.py — full test suite for the Intermediate example.

Strategy
--------
* Uses SQLite + RLS isolation for tests (no Postgres required).
* Tables are created fresh per test function via fixtures.
* Subdomain resolution is replaced with header resolution for test
  simplicity (no DNS tricks needed).
* app.router.lifespan_context is patched so our fixture controls
  store + config — no env-var monkey-patching needed.

Key correctness points
----------------------
* TenancyManager takes NO ``app`` argument — wiring is done via
  app.add_middleware() inside the lifespan before yield.
* SQLAlchemyTenantStore is the canonical class name.
* initialize_tenant() requires the ``metadata`` kwarg to create tables.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from fastapi_tenancy import Tenant, TenancyConfig, TenancyManager
from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
from fastapi_tenancy.storage.memory import InMemoryTenantStore

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def store() -> InMemoryTenantStore:
    s = InMemoryTenantStore()
    await s.create(Tenant(id="t-acme",   identifier="acme-corp", name="Acme Corp"))
    await s.create(Tenant(id="t-globex", identifier="globex",    name="Globex LLC"))
    return s


@pytest_asyncio.fixture
async def client(store: InMemoryTenantStore) -> AsyncClient:
    """
    Wire the Invoicer app to SQLite + in-memory store.

    We patch the lifespan so our fixture's store and config are used,
    then restore the original lifespan after the test.
    """
    from app.main import app
    from app.models import Base

    # Create tables in a dedicated engine (shared across the test)
    engine = create_async_engine(SQLITE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    # Use header resolution for simplicity — no Host header tricks needed
    config = TenancyConfig(
        database_url=SQLITE_URL,
        resolution_strategy="header",
        isolation_strategy="rls",
        tenant_header_name="X-Tenant-ID",
    )

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def patched_lifespan(a: object):
        manager = TenancyManager(config, tenant_store=store)
        # Middleware registered before yield — mandatory
        app.add_middleware(TenancyMiddleware, config=config, manager=manager)
        app.state.tenancy_manager = manager
        await manager.initialize()
        app.state.tenant_store       = manager.tenant_store
        app.state.isolation_provider = manager.isolation_provider
        try:
            yield
        finally:
            await manager.shutdown()

    app.router.lifespan_context = patched_lifespan

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.router.lifespan_context = original_lifespan


def acme() -> dict:
    return {"X-Tenant-ID": "acme-corp"}


def globex() -> dict:
    return {"X-Tenant-ID": "globex"}


# ── Health ─────────────────────────────────────────────────────────────────────

class TestHealth:
    @pytest.mark.asyncio
    async def test_health(self, client: AsyncClient) -> None:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── Customers ──────────────────────────────────────────────────────────────────

class TestCustomers:
    @pytest.mark.asyncio
    async def test_create_and_list_customers(self, client: AsyncClient) -> None:
        r = await client.post(
            "/customers",
            json={"name": "Alice Smith", "email": "alice@acme.example"},
            headers=acme(),
        )
        assert r.status_code == 201
        assert r.json()["email"] == "alice@acme.example"

        r2 = await client.get("/customers", headers=acme())
        assert r2.status_code == 200
        assert len(r2.json()) == 1

    @pytest.mark.asyncio
    async def test_duplicate_email_rejected(self, client: AsyncClient) -> None:
        await client.post(
            "/customers",
            json={"name": "Bob", "email": "bob@acme.example"},
            headers=acme(),
        )
        r = await client.post(
            "/customers",
            json={"name": "Bob2", "email": "bob@acme.example"},
            headers=acme(),
        )
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_get_customer_not_found(self, client: AsyncClient) -> None:
        r = await client.get("/customers/99999", headers=acme())
        assert r.status_code == 404


# ── Invoices ───────────────────────────────────────────────────────────────────

class TestInvoices:
    @pytest_asyncio.fixture
    async def customer_id(self, client: AsyncClient) -> int:
        r = await client.post(
            "/customers",
            json={"name": "Test Customer", "email": "test@acme.example"},
            headers=acme(),
        )
        return r.json()["id"]

    @pytest.mark.asyncio
    async def test_create_invoice(
        self, client: AsyncClient, customer_id: int
    ) -> None:
        r = await client.post(
            "/invoices",
            json={
                "customer_id": customer_id,
                "number": "INV-001",
                "amount": "1500.00",
                "currency": "USD",
                "line_items": [
                    {"description": "Consulting", "quantity": "10", "unit_price": "150.00"}
                ],
            },
            headers=acme(),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["number"] == "INV-001"
        assert data["status"] == "draft"

    @pytest.mark.asyncio
    async def test_invoice_status_flow(
        self, client: AsyncClient, customer_id: int
    ) -> None:
        r = await client.post(
            "/invoices",
            json={"customer_id": customer_id, "number": "INV-002", "amount": "500.00"},
            headers=acme(),
        )
        invoice_id = r.json()["id"]

        r2 = await client.patch(
            f"/invoices/{invoice_id}/status",
            json={"status": "sent"},
            headers=acme(),
        )
        assert r2.json()["status"] == "sent"

        r3 = await client.patch(
            f"/invoices/{invoice_id}/status",
            json={"status": "paid"},
            headers=acme(),
        )
        assert r3.json()["status"] == "paid"

    @pytest.mark.asyncio
    async def test_duplicate_invoice_number(
        self, client: AsyncClient, customer_id: int
    ) -> None:
        payload = {"customer_id": customer_id, "number": "INV-DUP", "amount": "100.00"}
        await client.post("/invoices", json=payload, headers=acme())
        r = await client.post("/invoices", json=payload, headers=acme())
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_delete_invoice(
        self, client: AsyncClient, customer_id: int
    ) -> None:
        r = await client.post(
            "/invoices",
            json={"customer_id": customer_id, "number": "INV-DEL", "amount": "200.00"},
            headers=acme(),
        )
        invoice_id = r.json()["id"]
        del_r = await client.delete(f"/invoices/{invoice_id}", headers=acme())
        assert del_r.status_code == 204


# ── Schema isolation ───────────────────────────────────────────────────────────

class TestSchemaIsolation:
    """
    Acme customers and invoices must be invisible to Globex.
    With schema isolation each tenant has completely separate tables —
    isolation is structural, not just filtered queries.
    """

    @pytest.mark.asyncio
    async def test_customers_isolated(self, client: AsyncClient) -> None:
        await client.post(
            "/customers",
            json={"name": "Acme Employee", "email": "emp@acme.example"},
            headers=acme(),
        )
        r = await client.get("/customers", headers=globex())
        assert r.status_code == 200
        assert r.json() == []

    @pytest.mark.asyncio
    async def test_invoices_isolated(self, client: AsyncClient) -> None:
        c_r = await client.post(
            "/customers",
            json={"name": "Acme Co", "email": "co@acme.example"},
            headers=acme(),
        )
        cid = c_r.json()["id"]
        await client.post(
            "/invoices",
            json={"customer_id": cid, "number": "A-001", "amount": "999.00"},
            headers=acme(),
        )

        r = await client.get("/invoices", headers=globex())
        assert r.status_code == 200
        assert r.json() == []
