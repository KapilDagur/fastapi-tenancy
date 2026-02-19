# Configuration Reference

All configuration is done through `TenancyConfig`, a Pydantic settings model.

```python
from fastapi_tenancy import TenancyConfig

config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    resolution_strategy="header",
    isolation_strategy="schema",
)
```

Environment variables are also supported (prefix `TENANCY_`):

```bash
export TENANCY_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/myapp"
export TENANCY_ISOLATION_STRATEGY="rls"
```

---

## Core settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `database_url` | `str` | **required** | Any async SQLAlchemy URL. Sync drivers emit a warning. |
| `resolution_strategy` | `str` | `"header"` | One of `header`, `subdomain`, `path`, `jwt`, `custom`. |
| `isolation_strategy` | `str` | `"schema"` | One of `schema`, `database`, `rls`, `hybrid`. |
| `debug` | `bool` | `false` | Enable verbose logging and debug response headers. |

---

## Database pool

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `database_pool_size` | `int` | `5` | SQLAlchemy pool size (ignored for SQLite). |
| `database_max_overflow` | `int` | `10` | Max connections above `pool_size`. |
| `database_pool_timeout` | `float` | `30.0` | Seconds to wait for a connection. |
| `database_pool_recycle` | `float` | `3600.0` | Seconds before recycling a connection. |
| `database_echo` | `bool` | `false` | Log all SQL statements (development only). |
| `database_url_template` | `str \| None` | `None` | URL template for database-per-tenant strategy. Use `{database_name}` or `{tenant_id}` as placeholders. |

---

## Schema isolation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `schema_prefix` | `str` | `"tenant_"` | Prefix for auto-generated schema names. |

Tenant `acme-corp` → schema `tenant_acme_corp` (underscores replace hyphens).

Override per-tenant via `Tenant.schema_name`.

---

## Tenant resolution

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `header_name` | `str` | `"X-Tenant-ID"` | HTTP header for `header` strategy. |
| `domain_suffix` | `str \| None` | `None` | Domain suffix for `subdomain` strategy (e.g. `"example.com"`). |
| `path_prefix` | `str` | `"/tenants/"` | URL prefix for `path` strategy. |
| `jwt_tenant_claim` | `str` | `"tenant_id"` | JWT claim name for `jwt` strategy. |
| `jwt_secret_key` | `str \| None` | `None` | JWT verification secret. Set via `TENANCY_JWT_SECRET_KEY` env var. |
| `jwt_algorithm` | `str` | `"HS256"` | JWT signing algorithm. |

---

## Hybrid isolation

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `premium_isolation_strategy` | `IsolationStrategy` | `schema` | Strategy for premium tenants. |
| `standard_isolation_strategy` | `IsolationStrategy` | `rls` | Strategy for standard tenants. |

---

## Redis cache

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `redis_url` | `str \| None` | `None` | Redis connection URL. If `None`, caching is disabled. |
| `cache_ttl_seconds` | `int` | `300` | Tenant cache TTL. |
| `cache_prefix` | `str` | `"tenancy:"` | Redis key prefix. |

---

## Lifecycle

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `auto_create_tenant` | `bool` | `false` | Create a tenant on first request if not found. |
| `auto_initialize_tenant` | `bool` | `true` | Run `initialize_tenant()` after `create()`. |

---

## Example: full PostgreSQL production config

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@db-host:5432/myapp",
    resolution_strategy="subdomain",
    domain_suffix="myapp.io",
    isolation_strategy="schema",
    schema_prefix="t_",
    database_pool_size=10,
    database_max_overflow=20,
    redis_url="redis://redis-host:6379/0",
    cache_ttl_seconds=600,
    jwt_secret_key="super-secret",
)
```

## Example: SQLite development config

```python
config = TenancyConfig(
    database_url="sqlite+aiosqlite:///./dev.db",
    resolution_strategy="header",
    isolation_strategy="schema",   # automatically uses prefix mode
)
```
