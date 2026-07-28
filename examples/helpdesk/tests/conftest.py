"""Fixtures for the helpdesk example.

Everything runs against a **real PostgreSQL** started by Testcontainers. That
is not gold-plating: under SQLite the SCHEMA provider falls back to table-name
prefixing, so a SQLite suite would exercise a different isolation mechanism
than the one this example ships with — and the whole point is to prove the
isolation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from _helpers import OpenTicket, Provision
from asgi_lifespan import LifespanManager
from fastapi_tenancy import IsolationStrategy, ResolutionStrategy, TenancyConfig
import httpx
import pytest

from helpdesk.app import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

_PG_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Start one PostgreSQL container for the whole session."""
    docker = pytest.importorskip("docker", reason="Docker SDK not installed")
    try:
        docker.from_env().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Docker is not available: {exc}")

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(_PG_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture
async def client(postgres_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client bound to a fully started app.

    ``LifespanManager`` is what actually runs startup/shutdown — httpx's
    ``ASGITransport`` alone does not, which silently leaves the tenant store
    uninitialised and every request failing on a missing ``tenants`` table.
    """
    config = TenancyConfig(
        database_url=postgres_url,
        isolation_strategy=IsolationStrategy.SCHEMA,
        resolution_strategy=ResolutionStrategy.HEADER,
        tenant_header_name="X-Tenant-ID",
        l1_cache_enabled=True,
        enable_rate_limiting=False,
    )
    app = create_app(config)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://helpdesk.test",
        ) as http,
    ):
        yield http


@pytest.fixture(autouse=True)
async def _clean_database(postgres_url: str) -> AsyncIterator[None]:
    """Drop every tenant schema and clear the registry between tests.

    The container is session-scoped for speed, so without this each test
    inherits the previous one's tenants and the second ``provision`` of a
    given slug returns 409.  Cleaning *after* each test also leaves the
    database inspectable when a test fails.
    """
    yield

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(postgres_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            # Filter in Python rather than with LIKE: the escape clause needed
            # to treat "_" literally is a well-known footgun, and getting it
            # subtly wrong makes the cleanup silently match nothing.
            rows = await conn.execute(text("SELECT schema_name FROM information_schema.schemata"))
            names = [r[0] for r in rows.all() if str(r[0]).startswith("tenant_")]
            for schema in names:
                # Identifier cannot be bound; it comes from the catalogue and
                # matched the tenant_ prefix, so it is not attacker-supplied.
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text("TRUNCATE TABLE tenants RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
def provision(client: httpx.AsyncClient) -> Provision:
    """Return a helper that provisions a tenant and asserts it succeeded."""

    async def _provision(identifier: str, plan: str = "pro") -> dict[str, Any]:
        resp = await client.post(
            "/admin/tenants",
            json={"identifier": identifier, "name": identifier.title(), "plan": plan},
        )
        assert resp.status_code == 201, resp.text
        created: dict[str, Any] = resp.json()
        return created

    return _provision


@pytest.fixture
def open_ticket(client: httpx.AsyncClient) -> OpenTicket:
    """Return a helper that opens a ticket for a given tenant."""

    async def _open(tenant: str, subject: str, body: str = "") -> dict[str, Any]:
        resp = await client.post(
            "/tickets",
            headers={"X-Tenant-ID": tenant},
            json={
                "subject": subject,
                "body": body,
                "requester_email": f"user@{tenant}.test",
            },
        )
        assert resp.status_code == 201, resp.text
        created: dict[str, Any] = resp.json()
        return created

    return _open
