"""
tests/integration/test_api.py

Integration tests — real SQLite DB, real service layer, mocked JWT auth.
Tests the full HTTP request/response cycle including middleware.
No Docker / Postgres required.

Key correctness:
  - TenancyManager takes NO ``app`` argument
  - Middleware registered via app.add_middleware() inside patched lifespan
  - setup_middleware() does NOT exist
  - SQLAlchemyTenantStore is the canonical class name
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
JWT_SECRET = "test-secret-key-that-is-32-chars-long!!"


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_jwt(tenant_identifier: str) -> str:
    """Create a valid JWT for the given tenant identifier."""
    try:
        from jose import jwt
    except ImportError:
        pytest.skip("python-jose not installed — skipping JWT tests")
    return jwt.encode(
        {"tenant_id": tenant_identifier},
        JWT_SECRET,
        algorithm="HS256",
    )


def auth(tenant_identifier: str) -> dict:
    return {"Authorization": f"Bearer {make_jwt(tenant_identifier)}"}


# ── Fixtures ──────────────────────────────────────────────────────────────────

TENANTS = [
    Tenant(
        id="tenant-acme-001",
        identifier="acme-corp",
        name="Acme Corporation",
        metadata={"plan": "enterprise", "max_projects": 500},
    ),
    Tenant(
        id="tenant-startup-001",
        identifier="startup-x",
        name="Startup X",
        metadata={"plan": "starter", "max_projects": 3},
    ),
]


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    import app.main as main_module
    from app.main import app
    from app.models import Base

    # Save originals so we can restore after the test
    original_url      = main_module.DATABASE_URL
    original_secret   = main_module.JWT_SECRET
    original_premium  = main_module.PREMIUM_TENANTS

    main_module.DATABASE_URL    = SQLITE_URL
    main_module.JWT_SECRET      = JWT_SECRET
    main_module.PREMIUM_TENANTS = ["tenant-acme-001"]

    # Create tables in a single shared engine for this test
    engine = create_async_engine(SQLITE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()

    # In-memory store with test tenants
    store = InMemoryTenantStore()
    for t in TENANTS:
        await store.create(t)

    config = TenancyConfig(
        database_url=SQLITE_URL,
        resolution_strategy="jwt",
        # Use RLS on SQLite for tests (hybrid needs PostgreSQL for schema isolation)
        isolation_strategy="rls",
        jwt_secret=JWT_SECRET,
        jwt_algorithm="HS256",
        jwt_tenant_claim="tenant_id",
        premium_tenants=["tenant-acme-001"],
    )

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def patched_lifespan(a: object):
        manager = TenancyManager(config, tenant_store=store)
        # Middleware MUST be registered before yield
        app.add_middleware(
            TenancyMiddleware,
            config=config,
            manager=manager,
            skip_paths=["/health", "/auth", "/metrics", "/docs", "/redoc", "/openapi.json"],
        )
        app.state.tenancy_manager    = manager
        app.state.tenancy_config     = config
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
    main_module.DATABASE_URL    = original_url
    main_module.JWT_SECRET      = original_secret
    main_module.PREMIUM_TENANTS = original_premium


# ── Auth ───────────────────────────────────────────────────────────────────────

class TestAuth:
    @pytest.mark.asyncio
    async def test_get_token(self, client: AsyncClient) -> None:
        r = await client.post("/auth/token", json={"tenant_id": "acme-corp", "secret": "demo"})
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert data["tenant_id"] == "acme-corp"

    @pytest.mark.asyncio
    async def test_no_token_returns_error(self, client: AsyncClient) -> None:
        r = await client.get("/projects")   # no Authorization header
        assert r.status_code in (400, 401, 404)

    @pytest.mark.asyncio
    async def test_invalid_token_rejected(self, client: AsyncClient) -> None:
        r = await client.get(
            "/projects",
            headers={"Authorization": "Bearer not-a-jwt"},
        )
        assert r.status_code in (400, 401, 404)


# ── Health ─────────────────────────────────────────────────────────────────────

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_public(self, client: AsyncClient) -> None:
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── Projects CRUD ──────────────────────────────────────────────────────────────

class TestProjectsCRUD:
    @pytest.mark.asyncio
    async def test_create_project(self, client: AsyncClient) -> None:
        r = await client.post(
            "/projects",
            json={"name": "Mars Mission", "description": "Land on Mars by 2030"},
            headers=auth("acme-corp"),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "Mars Mission"
        assert "id" in data

    @pytest.mark.asyncio
    async def test_list_projects(self, client: AsyncClient) -> None:
        await client.post("/projects", json={"name": "P1"}, headers=auth("acme-corp"))
        await client.post("/projects", json={"name": "P2"}, headers=auth("acme-corp"))
        r = await client.get("/projects", headers=auth("acme-corp"))
        assert r.status_code == 200
        assert len(r.json()) >= 2

    @pytest.mark.asyncio
    async def test_get_project(self, client: AsyncClient) -> None:
        cr = await client.post("/projects", json={"name": "Fetchable"}, headers=auth("acme-corp"))
        pid = cr.json()["id"]
        r = await client.get(f"/projects/{pid}", headers=auth("acme-corp"))
        assert r.status_code == 200
        assert r.json()["name"] == "Fetchable"

    @pytest.mark.asyncio
    async def test_get_nonexistent_project(self, client: AsyncClient) -> None:
        r = await client.get("/projects/99999", headers=auth("acme-corp"))
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_update_project(self, client: AsyncClient) -> None:
        cr = await client.post("/projects", json={"name": "Old Name"}, headers=auth("acme-corp"))
        pid = cr.json()["id"]
        r = await client.patch(
            f"/projects/{pid}",
            json={"name": "New Name", "description": "Updated"},
            headers=auth("acme-corp"),
        )
        assert r.status_code == 200
        assert r.json()["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_delete_project(self, client: AsyncClient) -> None:
        cr = await client.post("/projects", json={"name": "Deletable"}, headers=auth("acme-corp"))
        pid = cr.json()["id"]
        dr = await client.delete(f"/projects/{pid}", headers=auth("acme-corp"))
        assert dr.status_code == 204
        gr = await client.get(f"/projects/{pid}", headers=auth("acme-corp"))
        assert gr.status_code == 404


# ── Tasks ──────────────────────────────────────────────────────────────────────

class TestTasks:
    @pytest_asyncio.fixture
    async def project_id(self, client: AsyncClient) -> int:
        r = await client.post(
            "/projects", json={"name": "Task Container"}, headers=auth("acme-corp")
        )
        return r.json()["id"]

    @pytest.mark.asyncio
    async def test_create_task(self, client: AsyncClient, project_id: int) -> None:
        r = await client.post(
            f"/projects/{project_id}/tasks",
            json={"title": "Write tests", "assignee": "alice"},
            headers=auth("acme-corp"),
        )
        assert r.status_code == 201
        data = r.json()
        assert data["title"] == "Write tests"
        assert data["status"] == "todo"

    @pytest.mark.asyncio
    async def test_update_task_status(self, client: AsyncClient, project_id: int) -> None:
        cr = await client.post(
            f"/projects/{project_id}/tasks",
            json={"title": "Deploy"},
            headers=auth("acme-corp"),
        )
        tid = cr.json()["id"]
        r = await client.patch(
            f"/tasks/{tid}/status",
            json={"status": "in_progress"},
            headers=auth("acme-corp"),
        )
        assert r.status_code == 200
        assert r.json()["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_list_tasks(self, client: AsyncClient, project_id: int) -> None:
        for i in range(3):
            await client.post(
                f"/projects/{project_id}/tasks",
                json={"title": f"Task {i}"},
                headers=auth("acme-corp"),
            )
        r = await client.get(f"/projects/{project_id}/tasks", headers=auth("acme-corp"))
        assert r.status_code == 200
        assert len(r.json()) == 3


# ── Plan limits ────────────────────────────────────────────────────────────────

class TestPlanLimits:
    @pytest.mark.asyncio
    async def test_starter_tenant_hits_project_limit(self, client: AsyncClient) -> None:
        """Startup-x has max_projects=3 — 4th project should return 429."""
        headers = auth("startup-x")
        for i in range(3):
            r = await client.post(
                "/projects", json={"name": f"Project {i}"}, headers=headers
            )
            assert r.status_code == 201

        r = await client.post("/projects", json={"name": "Over limit"}, headers=headers)
        assert r.status_code == 429


# ── Cross-tenant isolation ─────────────────────────────────────────────────────

class TestIsolation:
    @pytest.mark.asyncio
    async def test_projects_isolated_between_tenants(self, client: AsyncClient) -> None:
        for i in range(2):
            await client.post(
                "/projects", json={"name": f"Acme-{i}"}, headers=auth("acme-corp")
            )
        await client.post("/projects", json={"name": "Startup-0"}, headers=auth("startup-x"))

        acme_projects    = (await client.get("/projects", headers=auth("acme-corp"))).json()
        startup_projects = (await client.get("/projects", headers=auth("startup-x"))).json()

        acme_names    = {p["name"] for p in acme_projects}
        startup_names = {p["name"] for p in startup_projects}

        assert acme_names.isdisjoint(startup_names), (
            f"Isolation failure! Overlap: {acme_names & startup_names}"
        )

    @pytest.mark.asyncio
    async def test_cannot_access_other_tenants_project(self, client: AsyncClient) -> None:
        cr = await client.post(
            "/projects", json={"name": "Startup Secret"}, headers=auth("startup-x")
        )
        pid = cr.json()["id"]
        r = await client.get(f"/projects/{pid}", headers=auth("acme-corp"))
        assert r.status_code == 404
