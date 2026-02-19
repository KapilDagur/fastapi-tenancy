# Configuration Reference

`TenancyConfig` is a Pydantic Settings model. Every field has a corresponding
environment variable with the `TENANCY_` prefix.

```python
from fastapi_tenancy import TenancyConfig

# Programmatic
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    isolation_strategy="schema",
)

# From environment (all fields set via env vars)
config = TenancyConfig()
```

---

## Core settings

| Field | Type | Default | Env var | Description |
|---|---|---|---|---|
| `database_url` | `AnyUrl` | **required** | `TENANCY_DATABASE_URL` | Primary database URL |
| `resolution_strategy` | `ResolutionStrategy` | `"header"` | `TENANCY_RESOLUTION_STRATEGY` | How to identify the tenant |
| `isolation_strategy` | `IsolationStrategy` | `"schema"` | `TENANCY_ISOLATION_STRATEGY` | How to isolate tenant data |

## Database pool

| Field | Type | Default | Description |
|---|---|---|---|
| `database_pool_size` | `int` | `20` | Connections per pool (1–100) |
| `database_max_overflow` | `int` | `40` | Overflow connections (0–200) |
| `database_pool_timeout` | `int` | `30` | Pool checkout timeout (seconds) |
| `database_pool_recycle` | `int` | `3600` | Connection recycle interval (seconds) |
| `database_echo` | `bool` | `False` | Log all SQL (development only) |
| `database_url_template` | `str \| None` | `None` | URL template for DATABASE isolation |

## Tenant resolution

| Field | Type | Default | Description |
|---|---|---|---|
| `tenant_header_name` | `str` | `"X-Tenant-ID"` | Header name for HEADER strategy |
| `domain_suffix` | `str \| None` | `None` | Domain suffix for SUBDOMAIN strategy |
| `path_prefix` | `str` | `"/tenants"` | URL prefix for PATH strategy |
| `jwt_secret` | `str \| None` | `None` | Secret for JWT strategy (min 32 chars) |
| `jwt_algorithm` | `str` | `"HS256"` | JWT signing algorithm |
| `jwt_tenant_claim` | `str` | `"tenant_id"` | JWT claim containing tenant identifier |

## Hybrid strategy

| Field | Type | Default | Description |
|---|---|---|---|
| `premium_tenants` | `list[str]` | `[]` | Tenant IDs that receive premium isolation |
| `premium_isolation_strategy` | `IsolationStrategy` | `"schema"` | Strategy for premium tenants |
| `standard_isolation_strategy` | `IsolationStrategy` | `"rls"` | Strategy for standard tenants |

## Schema naming

| Field | Type | Default | Description |
|---|---|---|---|
| `schema_prefix` | `str` | `"tenant_"` | Prefix for schema names (must match `^[a-z][a-z0-9_]*$`) |
| `public_schema` | `str` | `"public"` | Shared schema name |

## Cache (optional — requires `[redis]` extra)

| Field | Type | Default | Description |
|---|---|---|---|
| `cache_enabled` | `bool` | `False` | Enable Redis cache layer |
| `redis_url` | `RedisDsn \| None` | `None` | Redis connection URL |
| `cache_ttl` | `int` | `3600` | Default TTL in seconds |

!!! warning "cache_enabled default is False"
    `cache_enabled` defaults to `False` (not `True`) to prevent runtime errors
    when `redis_url` is not set. Always set `redis_url` before enabling.

## Security

| Field | Type | Default | Description |
|---|---|---|---|
| `enable_rate_limiting` | `bool` | `True` | Rate limiting flag (reserved for v0.3) |
| `rate_limit_per_minute` | `int` | `100` | Default rate limit |
| `enable_audit_logging` | `bool` | `True` | Audit logging flag (reserved for v0.3) |
| `enable_encryption` | `bool` | `False` | Encryption flag (reserved for v0.3) |
| `encryption_key` | `str \| None` | `None` | Encryption key (required if enabled) |

!!! note "Reserved flags"
    `enable_rate_limiting`, `enable_audit_logging`, and `enable_encryption` are
    configuration flags whose implementations are planned for v0.3. They are
    validated (e.g. `encryption_key` must be set if `enable_encryption=True`)
    but do not yet apply any runtime behaviour.

## Tenant management

| Field | Type | Default | Description |
|---|---|---|---|
| `allow_tenant_registration` | `bool` | `False` | Allow self-service registration |
| `max_tenants` | `int \| None` | `None` | Max tenants allowed (None = unlimited) |
| `default_tenant_status` | `str` | `"active"` | Status for new tenants |
| `enable_soft_delete` | `bool` | `True` | Soft-delete tenants instead of hard-delete |

## Cross-field validation

`TenancyConfig.model_post_init()` runs `validate_configuration()` automatically,
so misconfigurations raise `ValueError` at construction time:

```python
# Raises: "cache_enabled requires redis_url to be set"
TenancyConfig(
    database_url="postgresql+asyncpg://...",
    cache_enabled=True,  # redis_url not set!
)

# Raises: "jwt_secret is required when using JWT resolution strategy"
TenancyConfig(
    database_url="postgresql+asyncpg://...",
    resolution_strategy="jwt",  # jwt_secret not set!
)
```
