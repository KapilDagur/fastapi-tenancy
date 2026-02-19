# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- Nothing yet.

---

## [0.2.0] — 2026-02-16

### Added
- **Multi-database support** — automatic dialect detection for PostgreSQL, SQLite, MySQL, MSSQL via `DbDialect` enum in `utils/db_compat.py`.
- `SchemaIsolationProvider` now degrades gracefully to **table-name prefix mode** (`t_<slug>_<table>`) on SQLite / unknown dialects — no code changes needed when switching dev↔prod.
- `RLSIsolationProvider` falls back to explicit `WHERE tenant_id = :id` filter on non-PostgreSQL dialects.
- `DatabaseIsolationProvider` creates per-tenant `.db` files on SQLite; raises clear `IsolationError` on MSSQL (manual creation required).
- `TenantCache` — full async Redis cache with `get`, `set`, `delete`, `exists`, `get_ttl`, `increment`, `clear_tenant`, `get_keys`, `get_stats`.
- `TenancyManager.health_check()` and `get_metrics()` public methods.
- `TenancyManager.tenant_scope()` async context manager.
- `TenancyManager.create_lifespan()` static helper for clean FastAPI integration.
- `TenancyMiddleware.should_skip()` public helper (was private `_should_skip`).
- `SchemaIsolationProvider.get_schema_name()` public method (patchable in tests).
- `require_active_tenant` and `get_tenant_config` FastAPI dependency functions.
- `tox.ini` — multi-environment test matrix (Python 3.11, 3.12, 3.13; SQLite + PostgreSQL).
- `.devcontainer/devcontainer.json` + `Dockerfile` — VS Code Dev Container with PostgreSQL + Redis.
- `docker-compose.yml` — local dev services.
- `Makefile` — `make test`, `make lint`, `make format`, `make typecheck`, `make build`, etc.
- `.pre-commit-config.yaml` — ruff format + lint + mypy on every commit.
- GitHub Actions CI: lint → SQLite matrix → PostgreSQL integration → build check.

### Fixed
- **`async for` → `async with`** in `SchemaIsolationProvider.get_session()` — `@asynccontextmanager` functions are context managers, not async iterators. All three branches (MySQL, native-schema, prefix) fixed.
- **`UnboundLocalError: slug`** in `DatabaseIsolationProvider._build_tenant_url()` — `slug` was used before assignment in SQLite branch.
- **`TenancyMiddleware.__init__()` required `config`** — `config` is now optional (`None` default) for test-friendly construction.
- **`skip_paths=[]` ignored** — `skip_paths or defaults` was falsy for empty list; changed to `if skip_paths is not None`.
- **`apply_filters` outside class** — method was appended after `__all__` in `schema.py` and invisible to Python. Moved inside `SchemaIsolationProvider`.
- **`CompileError: No literal value renderer for NULL`** in `apply_filters` — replaced `text().bindparams()` with `literal_column() == literal()` which supports `literal_binds=True`.
- **Hybrid factory test** — `MagicMock` for `premium_isolation_strategy` replaced with real `IsolationStrategy.SCHEMA` / `RLS` values.
- **`DeprecationWarning: Column.copy()` (SA 1.4)** — replaced with explicit `Column(name, type_, ...)` reconstruction in `_initialize_prefix`.
- **`_get_schema_name` raises `ValueError` not `IsolationError`** — now wraps `ValueError` from `assert_safe_schema_name` in `IsolationError` for a consistent exception contract.
- **Deprecated `asyncio.get_event_loop().run_until_complete()`** in test fixture — replaced with `async def` fixture using `await`.
- **`TenancyMiddleware.resolver` raised `RuntimeError` when unconfigured** — now returns `None`; `dispatch()` handles the missing-resolver case cleanly.

### Changed
- `TenancyConfig.database_url` validator relaxed — accepts any async SQLAlchemy URL; emits a `UserWarning` (not `ValidationError`) for sync drivers.
- `SchemaIsolationProvider._get_schema_name()` now calls `self.get_schema_name()` (the patchable public method) before validation.
- `_initialize_prefix()` uses SA 2.0-compatible column reconstruction instead of deprecated `Column.copy()`.

### Removed
- Nothing removed.

---

## [0.1.0] — 2024-12-01

### Added
- Initial release.
- `TenancyManager`, `TenancyMiddleware`, `TenantContext`.
- `HeaderTenantResolver`, `SubdomainTenantResolver`, `PathTenantResolver`, `JWTTenantResolver`.
- `SchemaIsolationProvider`, `DatabaseIsolationProvider`, `RLSIsolationProvider`, `HybridIsolationProvider`.
- `PostgreSQLTenantStore`, `InMemoryTenantStore`, `RedisTenantStore`.
- Alembic migration manager with per-tenant upgrade/downgrade.

[Unreleased]: https://github.com/yourusername/fastapi-tenancy/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/yourusername/fastapi-tenancy/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/yourusername/fastapi-tenancy/releases/tag/v0.1.0
