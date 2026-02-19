"""
tests/e2e/test_live.py

End-to-end tests against the running Docker Compose stack.
Requires: docker compose up --build (takes ~30s first time)

Run:
    pytest tests/e2e/ -v -m e2e
    pytest tests/e2e/ -v -m e2e --base-url http://localhost:8000

These tests exercise the FULL stack including:
  - Real PostgreSQL with RLS + schema isolation
  - Real Redis caching
  - JWT token flow
  - Per-tenant schema creation
"""
from __future__ import annotations

import os

import pytest
import httpx

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")


def get_token(tenant_identifier: str) -> str:
    r = httpx.post(
        f"{BASE_URL}/auth/token",
        json={"tenant_id": tenant_identifier, "secret": "demo"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def auth(tenant_identifier: str) -> dict:
    return {"Authorization": f"Bearer {get_token(tenant_identifier)}"}


# ── Smoke tests ───────────────────────────────────────────────────────────────

@pytest.mark.e2e
class TestSmoke:

    def test_health_endpoint(self) -> None:
        r = httpx.get(f"{BASE_URL}/health", timeout=10)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_docs_accessible(self) -> None:
        r = httpx.get(f"{BASE_URL}/docs", timeout=10)
        assert r.status_code == 200

    def test_auth_flow(self) -> None:
        r = httpx.post(
            f"{BASE_URL}/auth/token",
            json={"tenant_id": "acme-corp", "secret": "demo"},
            timeout=10,
        )
        assert r.status_code == 200
        token = r.json()["access_token"]
        assert len(token) > 50  # JWT is always > 50 chars


# ── Enterprise tenant (schema isolation) ──────────────────────────────────────

@pytest.mark.e2e
class TestEnterpriseTenant:
    """Acme Corp is a premium/enterprise tenant — gets a dedicated schema."""

    def test_me_shows_enterprise_tier(self) -> None:
        r = httpx.get(f"{BASE_URL}/me", headers=auth("acme-corp"), timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["tier"] == "enterprise"
        assert data["plan"] == "enterprise"

    def test_create_and_fetch_project(self) -> None:
        token = get_token("acme-corp")
        headers = {"Authorization": f"Bearer {token}"}

        # Create
        cr = httpx.post(
            f"{BASE_URL}/projects",
            json={"name": "E2E Test Project", "description": "Created by e2e test"},
            headers=headers,
            timeout=10,
        )
        assert cr.status_code == 201
        pid = cr.json()["id"]

        # Fetch
        gr = httpx.get(f"{BASE_URL}/projects/{pid}", headers=headers, timeout=10)
        assert gr.status_code == 200
        assert gr.json()["name"] == "E2E Test Project"

        # Clean up
        httpx.delete(f"{BASE_URL}/projects/{pid}", headers=headers, timeout=10)

    def test_create_task_on_project(self) -> None:
        token = get_token("acme-corp")
        headers = {"Authorization": f"Bearer {token}"}

        # Create project
        pr = httpx.post(
            f"{BASE_URL}/projects",
            json={"name": "Task E2E"},
            headers=headers,
            timeout=10,
        )
        pid = pr.json()["id"]

        # Create task
        tr = httpx.post(
            f"{BASE_URL}/projects/{pid}/tasks",
            json={"title": "E2E Task", "assignee": "e2e-runner"},
            headers=headers,
            timeout=10,
        )
        assert tr.status_code == 201
        tid = tr.json()["id"]
        assert tr.json()["status"] == "todo"

        # Update status
        ur = httpx.patch(
            f"{BASE_URL}/tasks/{tid}/status",
            json={"status": "done"},
            headers=headers,
            timeout=10,
        )
        assert ur.status_code == 200
        assert ur.json()["status"] == "done"

        # Clean up
        httpx.delete(f"{BASE_URL}/projects/{pid}", headers=headers, timeout=10)


# ── Starter tenant (RLS isolation) ────────────────────────────────────────────

@pytest.mark.e2e
class TestStarterTenant:
    """Startup-X is a standard/starter tenant — shared schema with RLS."""

    def test_me_shows_starter_tier(self) -> None:
        r = httpx.get(f"{BASE_URL}/me", headers=auth("startup-x"), timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert data["tier"] == "starter"

    def test_project_limit_enforced(self) -> None:
        token = get_token("startup-x")
        headers = {"Authorization": f"Bearer {token}"}

        # Get current project count
        existing = httpx.get(f"{BASE_URL}/projects", headers=headers, timeout=10).json()
        limit = 3  # from metadata max_projects

        created_ids = []
        for i in range(limit - len(existing)):
            r = httpx.post(
                f"{BASE_URL}/projects",
                json={"name": f"E2E-{i}"},
                headers=headers,
                timeout=10,
            )
            if r.status_code == 201:
                created_ids.append(r.json()["id"])

        # One more should hit the limit
        over = httpx.post(
            f"{BASE_URL}/projects",
            json={"name": "Over limit"},
            headers=headers,
            timeout=10,
        )
        assert over.status_code == 429

        # Clean up
        for pid in created_ids:
            httpx.delete(f"{BASE_URL}/projects/{pid}", headers=headers, timeout=10)


# ── Cross-tenant isolation ─────────────────────────────────────────────────────

@pytest.mark.e2e
class TestCrossTenantIsolation:
    """The most critical test: one tenant cannot see another's data."""

    def test_projects_are_isolated(self) -> None:
        acme_headers    = auth("acme-corp")
        startup_headers = auth("startup-x")

        # Create a uniquely named project for Acme
        import uuid
        unique = str(uuid.uuid4())[:8]
        pr = httpx.post(
            f"{BASE_URL}/projects",
            json={"name": f"ACME-PRIVATE-{unique}"},
            headers=acme_headers,
            timeout=10,
        )
        assert pr.status_code == 201
        pid = pr.json()["id"]

        # Startup-X should NOT see this project
        startup_projects = httpx.get(
            f"{BASE_URL}/projects", headers=startup_headers, timeout=10
        ).json()
        startup_names = {p["name"] for p in startup_projects}
        assert f"ACME-PRIVATE-{unique}" not in startup_names

        # Startup-X should get 404 when fetching by ID
        r = httpx.get(f"{BASE_URL}/projects/{pid}", headers=startup_headers, timeout=10)
        assert r.status_code == 404

        # Clean up
        httpx.delete(f"{BASE_URL}/projects/{pid}", headers=acme_headers, timeout=10)
