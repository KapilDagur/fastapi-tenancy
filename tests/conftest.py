"""Pytest configuration and shared fixtures."""

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from faker import Faker
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fastapi_tenancy.core.config import TenancyConfig
from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.types import (
    IsolationStrategy,
    ResolutionStrategy,
    Tenant,
    TenantStatus,
)

# Initialize Faker
fake = Faker()


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture()
def test_config() -> TenancyConfig:
    """Create test configuration."""
    return TenancyConfig(
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",  # type: ignore
        redis_url=None,  # Tests without Redis by default
        resolution_strategy=ResolutionStrategy.HEADER,
        isolation_strategy=IsolationStrategy.SCHEMA,
        enable_rate_limiting=False,  # Disable for tests
        enable_audit_logging=False,  # Disable for tests
    )


@pytest.fixture()
def test_tenant() -> Tenant:
    """Create a test tenant."""
    return Tenant(
        id="test-tenant-1",
        identifier="acme",
        name="Acme Corporation",
        status=TenantStatus.ACTIVE,
        isolation_strategy=IsolationStrategy.SCHEMA,
        schema_name="tenant_acme",
    )


@pytest.fixture()
def secondary_tenant() -> Tenant:
    """Create a secondary test tenant."""
    return Tenant(
        id="test-tenant-2",
        identifier="globex",
        name="Globex Corporation",
        status=TenantStatus.ACTIVE,
        isolation_strategy=IsolationStrategy.SCHEMA,
        schema_name="tenant_globex",
    )


@pytest.fixture()
def suspended_tenant() -> Tenant:
    """Create a suspended tenant."""
    return Tenant(
        id="test-tenant-suspended",
        identifier="suspended",
        name="Suspended Corp",
        status=TenantStatus.SUSPENDED,
        isolation_strategy=IsolationStrategy.SCHEMA,
    )


@pytest.fixture()
def random_tenant() -> Tenant:
    """Create a random tenant using Faker."""
    company = fake.company()
    slug = company.lower().replace(" ", "-").replace(",", "")[:20]
    return Tenant(
        id=fake.uuid4(),
        identifier=slug,
        name=company,
        status=TenantStatus.ACTIVE,
        isolation_strategy=IsolationStrategy.SCHEMA,
        schema_name=f"tenant_{slug}",
    )


@pytest_asyncio.fixture
async def async_db_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create async database engine for testing."""
    engine = create_async_engine(
        "postgresql+asyncpg://test:test@localhost:5432/test",
        echo=False,
        poolclass=StaticPool,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(
    async_db_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    """Create async database session."""
    async_session_maker = async_sessionmaker(
        async_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session_maker() as session:
        yield session
        await session.rollback()


@pytest.fixture()
def sync_db_engine() -> Generator[Any, None, None]:
    """Create synchronous database engine for testing."""
    engine = create_engine(
        "postgresql://test:test@localhost:5432/test",
        echo=False,
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(sync_db_engine: Any) -> Generator[Session, None, None]:  # noqa: ANN401
    """Create synchronous database session."""
    SessionLocal = sessionmaker(  # noqa: N806
        autocommit=False, autoflush=False, bind=sync_db_engine
    )
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture()
def fastapi_app(test_config: TenancyConfig) -> FastAPI:  # noqa: ARG001
    """Create FastAPI application with tenancy."""
    app = FastAPI(title="Test API")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/tenant-info")
    async def tenant_info() -> dict[str, Any]:
        tenant = TenantContext.get()
        return {
            "id": tenant.id,
            "identifier": tenant.identifier,
            "name": tenant.name,
        }

    return app


@pytest.fixture()
def test_client(fastapi_app: FastAPI) -> TestClient:
    """Create test client."""
    return TestClient(fastapi_app)


@pytest.fixture()
def mock_redis() -> MagicMock:
    """Create mock Redis client."""
    mock = MagicMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.exists = AsyncMock(return_value=0)
    mock.expire = AsyncMock(return_value=True)
    return mock


@pytest.fixture(autouse=True)
def _clear_tenant_context() -> Generator[None, None, None]:
    """Clear tenant context before and after each test."""
    TenantContext.clear()
    yield
    TenantContext.clear()


@pytest.fixture()
def tenant_factory() -> type:
    """Factory for creating test tenants."""

    class TenantFactory:
        @staticmethod
        def create(
            id: str | None = None,  # noqa: A002
            identifier: str | None = None,
            name: str | None = None,
            status: TenantStatus = TenantStatus.ACTIVE,
            **kwargs: Any,  # noqa: ANN401
        ) -> Tenant:
            """Create a tenant with optional overrides."""
            company = name if name else fake.company()
            slug = (
                identifier
                if identifier
                else company.lower().replace(" ", "-").replace(",", "")[:20]
            )
            return Tenant(
                id=id or fake.uuid4(),
                identifier=slug,
                name=company,
                status=status,
                isolation_strategy=kwargs.get("isolation_strategy", IsolationStrategy.SCHEMA),
                schema_name=kwargs.get("schema_name", f"tenant_{slug}"),
                **kwargs,
            )

        @staticmethod
        def create_batch(count: int, **kwargs: Any) -> list[Tenant]:  # noqa: ANN401
            """Create multiple tenants."""
            return [TenantFactory.create(**kwargs) for _ in range(count)]

    return TenantFactory


# Pytest configuration
def pytest_configure(config: Any) -> None:  # noqa: ANN401
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "e2e: End-to-end tests")
    config.addinivalue_line("markers", "performance: Performance tests")
    config.addinivalue_line("markers", "security: Security tests")
    config.addinivalue_line("markers", "slow: Slow running tests")
    config.addinivalue_line("markers", "requires_db: Tests requiring database")
    config.addinivalue_line("markers", "requires_redis: Tests requiring Redis")
