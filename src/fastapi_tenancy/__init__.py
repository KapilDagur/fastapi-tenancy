"""fastapi-tenancy — Enterprise-grade multi-tenancy for FastAPI.

Quick start
-----------
.. code-block:: python

    from fastapi import FastAPI
    from fastapi_tenancy import TenancyManager, TenancyConfig

    config = TenancyConfig(
        database_url="postgresql+asyncpg://user:pass@localhost/myapp",
        resolution_strategy="header",
        isolation_strategy="schema",
    )

    app = FastAPI(lifespan=TenancyManager.create_lifespan(config))

Public API
----------
Core types
    Tenant, TenantStatus, IsolationStrategy, ResolutionStrategy, TenantConfig

Configuration
    TenancyConfig

Manager & Middleware
    TenancyManager, TenancyMiddleware

Context helpers
    TenantContext, get_current_tenant, get_current_tenant_optional

FastAPI dependencies
    get_tenant_db, require_active_tenant, get_tenant_config

Storage backends
    TenantStore (ABC), InMemoryTenantStore, SQLAlchemyTenantStore

Cache
    TenantCache

Migrations
    MigrationManager

Exceptions
    TenancyError and all its subclasses
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__: str = _pkg_version("fastapi-tenancy")
except PackageNotFoundError:  # running from source without install
    __version__ = "0.0.0+dev"

__author__ = "fastapi-tenancy contributors"
__license__ = "MIT"

# Configuration
from fastapi_tenancy.core.config import TenancyConfig

# Context
from fastapi_tenancy.core.context import (
    TenantContext,
    get_current_tenant,
    get_current_tenant_optional,
)

# Exceptions
from fastapi_tenancy.core.exceptions import (
    ConfigurationError,
    DatabaseConnectionError,
    IsolationError,
    MigrationError,
    RateLimitExceededError,
    TenancyError,
    TenantDataLeakageError,
    TenantInactiveError,
    TenantNotFoundError,
    TenantQuotaExceededError,
    TenantResolutionError,
)
from fastapi_tenancy.core.types import (
    AuditLog,
    BaseIsolationProvider,
    IsolationStrategy,
    ResolutionStrategy,
    Tenant,
    TenantConfig,
    TenantMetrics,
    TenantStatus,
)

# Dependencies
from fastapi_tenancy.dependencies import (
    get_tenant_config,
    get_tenant_db,
    require_active_tenant,
)

# Manager
from fastapi_tenancy.manager import TenancyManager

# Middleware
from fastapi_tenancy.middleware.tenancy import TenancyMiddleware

# Migrations (optional — requires: pip install fastapi-tenancy[migrations])
try:
    from fastapi_tenancy.migrations.manager import MigrationManager
except ImportError:
    MigrationManager = None  # type: ignore[assignment, misc]

# Cache (optional — requires: pip install fastapi-tenancy[redis])
try:
    from fastapi_tenancy.cache.tenant_cache import TenantCache
except ImportError:
    TenantCache = None  # type: ignore[assignment, misc]
from fastapi_tenancy.resolution.base import BaseTenantResolver
from fastapi_tenancy.storage.memory import InMemoryTenantStore
from fastapi_tenancy.storage.postgres import (
    PostgreSQLTenantStore,
    SQLAlchemyTenantStore,
)

# Storage
from fastapi_tenancy.storage.tenant_store import TenantStore

__all__ = [  # noqa: RUF022
    "__version__",
    # Types
    "Tenant",
    "TenantStatus",
    "TenantConfig",
    "TenantMetrics",
    "AuditLog",
    "IsolationStrategy",
    "ResolutionStrategy",
    "BaseTenantResolver",
    "BaseIsolationProvider",
    # Config
    "TenancyConfig",
    # Context
    "TenantContext",
    "get_current_tenant",
    "get_current_tenant_optional",
    # Exceptions
    "TenancyError",
    "TenantNotFoundError",
    "TenantResolutionError",
    "TenantInactiveError",
    "IsolationError",
    "ConfigurationError",
    "MigrationError",
    "RateLimitExceededError",
    "TenantDataLeakageError",
    "TenantQuotaExceededError",
    "DatabaseConnectionError",
    # Manager
    "TenancyManager",
    # Middleware
    "TenancyMiddleware",
    # Dependencies
    "get_tenant_db",
    "require_active_tenant",
    "get_tenant_config",
    # Storage
    "TenantStore",
    "InMemoryTenantStore",
    "PostgreSQLTenantStore",
    "SQLAlchemyTenantStore",
    # Cache
    "TenantCache",
    # Migrations
    "MigrationManager",
]
