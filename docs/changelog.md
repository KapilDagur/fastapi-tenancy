# Changelog

All notable changes to `fastapi-tenancy` are documented here.  This project
follows [Semantic Versioning](https://semver.org/) — patch releases contain
only bug fixes, minor releases add features without breaking changes, and
major releases may break the public API.

---

## [0.2.0] — First public release

This is the first versioned release of `fastapi-tenancy`.

### Features

**Core architecture**

- `TenancyManager` — central lifecycle orchestrator; no `app` argument in
  `__init__`, making it independently constructable and testable.  The FastAPI
  `app` is only touched inside `create_lifespan`.
- `TenancyManager.create_lifespan()` — static factory that returns a properly
  wired FastAPI lifespan context manager.  Registers `TenancyMiddleware`
  before the lifespan `yield` (the only point Starlette allows middleware
  changes), then calls `initialize()`, `yield`s, and calls `shutdown()`.
- `async with TenancyManager(config) as m:` — context-manager protocol for
  clean use in tests.
- `manager.tenant_scope(tenant_id)` — async context manager for background
  tasks and workers that need to set tenant context outside a request.

**Isolation strategies**

- `SchemaIsolationProvider` — one PostgreSQL schema per tenant;
  table-name-prefix fallback for SQLite; delegates to
  `DatabaseIsolationProvider` on MySQL (where SCHEMA = DATABASE).
- `DatabaseIsolationProvider` — one database per tenant; supports PostgreSQL,
  MySQL, and SQLite (file-per-tenant); MSSQL raises a clear error.
- `RLSIsolationProvider` — Row-Level Security via `SET app.current_tenant`;
  explicit `WHERE tenant_id` filter fallback for non-PostgreSQL dialects.
  `destroy_tenant` is fully implemented — pass `metadata=` or `table_names=`
  to specify which tables to purge.
- `HybridIsolationProvider` — routes tenants to different strategies based
  on tier; shares a single `AsyncEngine` between sub-providers to avoid
  duplicate connection pools.

**Resolution strategies**

- `HeaderTenantResolver` — reads from a configurable HTTP header
  (`X-Tenant-ID` by default); case-insensitive matching by default; error
  responses never include the list of available request headers (security fix).
- `SubdomainTenantResolver` — extracts tenant from subdomain
  (`acme-corp.myapp.io`); handles multi-level subdomains.
- `PathTenantResolver` — extracts tenant from URL path
  (`/tenants/acme-corp/resource`).
- `JWTTenantResolver` — decodes a Bearer JWT and reads a configurable claim
  (`tenant_id` by default); requires `python-jose`.

**Storage backends**

- `SQLAlchemyTenantStore` (preferred alias) / `PostgreSQLTenantStore`
  (deprecated alias, emits `DeprecationWarning`) — async SQLAlchemy-backed
  store; works with PostgreSQL, MySQL, and SQLite.
- `InMemoryTenantStore` — thread-safe, fully async in-memory store for
  testing; no database required.
- `RedisTenantStore` — write-through cache on top of any primary store;
  reads from Redis on cache hit, writes always go to primary first.

**Context and dependencies**

- `TenantContext` — `contextvars.ContextVar`-based tenant context; fully
  async-safe; no cross-request leakage.  Default for `_tenant_metadata` is
  `None` (not `{}`) to prevent shared mutable default bugs.
- `get_current_tenant()` / `get_current_tenant_optional()` — FastAPI
  dependency functions.
- `get_tenant_db()` — yields an `AsyncSession` scoped to the current tenant.
- `get_tenant_config()` — returns a `TenantConfig` hydrated from
  `tenant.metadata`.
- `require_active_tenant()` — raises `403` if tenant is not active.

**Configuration**

- `TenancyConfig` — Pydantic v2 Settings model; reads from `TENANCY_*`
  environment variables; cross-field validation runs in `model_post_init`.
- `cache_enabled` defaults to `False` to prevent runtime errors when
  `redis_url` is absent.

**Middleware**

- `TenancyMiddleware` — consolidated `_is_path_skipped(path)` and
  `_should_skip_request(request)` helpers (replaces the inconsistent
  `should_skip` / `_should_skip` from the pre-release prototype).  All
  logging uses `%s` deferred formatting.

**Migrations**

- `MigrationManager` — Alembic integration; all sync Alembic calls wrapped in
  `run_in_executor` to prevent event-loop blocking; per-strategy schema/URL
  routing; `get_migration_status()` queries `alembic_version` directly.

**Cache**

- `TenantCache` — key-prefixed Redis cache with `{prefix}:tenant:{id}:`
  namespace for complete cross-tenant isolation.

**Multi-database compatibility**

- `detect_dialect()` — maps any async SQLAlchemy URL to a `DbDialect` enum.
- Compatibility matrix: PostgreSQL (full), SQLite (dev/CI), MySQL, MSSQL
  (partial), UNKNOWN (prefix fallback).

**Type safety**

- `py.typed` marker — package is PEP 561 compliant; mypy users get full type
  checking without additional stubs.
- `BaseIsolationProvider` and `BaseTenantResolver` are both `ABC` subclasses
  and `runtime_checkable Protocol`s.
- `Tenant` is a frozen Pydantic v2 model — immutable after construction.

**Testing infrastructure**

- `InMemoryTenantStore` test double.
- `tests/test_concurrent_isolation.py` — concurrent `asyncio.gather` tests
  verifying that `ContextVar` prevents cross-task tenant leakage, metadata
  isolation, nested scope restoration, and exception-safe context cleanup.
- pytest markers: `unit`, `integration`, `e2e`.
- Minimum coverage: 75 % enforced via `pytest-cov --fail-under`.

**CI/CD**

- `.github/workflows/ci.yml` — lint (ruff + mypy) + test matrix
  (Python 3.11 / 3.12 / 3.13) + security audit (pip-audit).

**Documentation**

- Full MkDocs + Material theme documentation site.
- API reference auto-generated from docstrings via `mkdocstrings`.
- Guides: isolation strategies, resolution strategies, middleware & lifespan,
  FastAPI dependencies, testing, migrations, production deployment.
- Examples: basic, intermediate (invoicing SaaS), advanced (hybrid + Celery).

---

## Roadmap

### 0.3.0 (planned)

- OpenTelemetry tracing integration
- Prometheus metrics exporter
- `enable_encryption` — field-level encryption for sensitive tenant metadata
- `enable_audit_logging` — structured audit log emission per tenant operation
- `enable_rate_limiting` — per-tenant rate limiting with Redis token bucket

### 1.0.0 (planned)

- Stable public API guarantee
- Remove deprecated `PostgreSQLTenantStore` name (use `SQLAlchemyTenantStore`)
- Django ORM adapter (optional extra)
