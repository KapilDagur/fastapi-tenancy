# Isolation Strategies

Isolation strategies control **how tenant data is separated at the database level**.
Choosing the right strategy depends on your security requirements, scale, and budget.

---

## Strategy comparison

| Strategy | Isolation level | Cost | Best for |
|---|---|---|---|
| `schema` | Strong — separate namespace | Low | PostgreSQL SaaS, standard choice |
| `database` | Strongest — separate DB file | Medium | Maximum compliance, regulated industries |
| `rls` | Shared tables, policy-enforced | Lowest | High tenant count (1000+), cost optimization |
| `hybrid` | Tiered — premium/standard | Flexible | Mixed plans (enterprise + freemium) |

---

## Schema isolation (`isolation_strategy="schema"`)

One PostgreSQL schema per tenant. All tables exist in each schema.
`search_path` is set per-session so queries automatically hit the right schema.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    isolation_strategy="schema",
    schema_prefix="tenant_",   # results in: tenant_acme_corp, tenant_widgets_inc
)
```

**How it works (PostgreSQL):**
```sql
-- On each request session:
SET search_path TO tenant_acme_corp, public;

-- Queries resolve to tenant schema automatically:
SELECT * FROM users;   -- actually: SELECT * FROM tenant_acme_corp.users
```

**SQLite fallback**: table-name prefix (`t_acme_corp_users`, `t_acme_corp_orders`)

**MySQL**: automatically delegates to DATABASE isolation (MySQL `SCHEMA == DATABASE`).

!!! tip "Schema naming"
    Schema names are validated with `assert_safe_schema_name()` before any DDL.
    Names are double-quoted in SQL to prevent injection.
    Valid pattern: `^[a-z][a-z0-9_]*$`

### Initialize a tenant schema

```python
from myapp.models import Base   # your SQLAlchemy metadata

await isolation_provider.initialize_tenant(tenant, metadata=Base.metadata)
# → CREATE SCHEMA IF NOT EXISTS "tenant_acme_corp"
# → SET search_path TO tenant_acme_corp, public
# → Base.metadata.create_all(conn)  — creates all tables in the schema
```

### Destroy a tenant schema

```python
await isolation_provider.destroy_tenant(tenant)
# → DROP SCHEMA IF EXISTS "tenant_acme_corp" CASCADE
```

---

## Database isolation (`isolation_strategy="database"`)

One database per tenant. Maximum isolation — tenants cannot even share
connection infrastructure.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://admin:pass@localhost/main",
    isolation_strategy="database",
    database_url_template="postgresql+asyncpg://user:pass@localhost/{database_name}",
)
```

**How it works:**
```sql
-- On tenant create:
CREATE DATABASE "tenant_acme_corp_db";

-- Each session connects to the tenant database:
-- postgresql+asyncpg://user:pass@localhost/tenant_acme_corp_db
```

!!! note "MSSQL not supported"
    `DatabaseIsolationProvider` raises `IsolationError` for MSSQL.
    Use `schema` isolation or create databases manually.

---

## RLS isolation (`isolation_strategy="rls"`)

All tenants share the same tables. Row-Level Security policies (PostgreSQL)
or explicit `WHERE tenant_id = :id` filters separate the data.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    isolation_strategy="rls",
)
```

### PostgreSQL setup (one-time DDL per table)

```sql
-- Add tenant_id column
ALTER TABLE orders ADD COLUMN tenant_id TEXT NOT NULL;

-- Enable RLS
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

-- Create isolation policy
CREATE POLICY tenant_isolation ON orders
    USING (tenant_id = current_setting('app.current_tenant'));
```

### How it works in the provider

For PostgreSQL, `get_session()` sets the session variable:
```sql
SET app.current_tenant = 'tenant-id-123';
-- RLS policy filters automatically from here
```

For all other databases, `apply_filters()` adds `WHERE tenant_id = :id`.

### Destroying RLS tenant data

RLS uses shared tables, so `destroy_tenant` requires table information:

```python
# Explicit table names
await isolation_provider.destroy_tenant(
    tenant,
    table_names=["orders", "users", "invoices"],
)

# Or pass your SQLAlchemy metadata (auto-detects tenant_id columns)
from myapp.models import Base
await isolation_provider.destroy_tenant(tenant, metadata=Base.metadata)
```

---

## Hybrid isolation (`isolation_strategy="hybrid"`)

Route tenants to different strategies based on their tier.
Premium tenants get dedicated resources; standard tenants share.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    isolation_strategy="hybrid",
    premium_isolation_strategy="schema",  # dedicated schema per premium tenant
    standard_isolation_strategy="rls",    # shared tables for standard tenants
    premium_tenants=["acme-corp-001", "big-enterprise-002"],
)
```

!!! tip "Shared connection pool"
    `HybridIsolationProvider` creates a **single** `AsyncEngine` and shares it
    between both sub-providers. This prevents duplicate connection pools when
    both strategies target the same database.

### Promoting a tenant

```python
# Add tenant to premium list at runtime (requires config reload or restart)
# Or use a custom HybridIsolationProvider that reads from the database

# When a tenant is promoted, migrate their data to a dedicated schema:
await isolation_provider.premium_provider.initialize_tenant(tenant, metadata=Base.metadata)
```

---

## Multi-database compatibility

| Feature | PostgreSQL | SQLite | MySQL | MSSQL |
|---|---|---|---|---|
| Schema isolation | ✅ Native | ⚠️ Table prefix | ⚠️ → Database | ✅ Partial |
| Database isolation | ✅ | ✅ File-per-tenant | ✅ | ❌ Manual |
| RLS (native) | ✅ | ❌ → WHERE filter | ❌ → WHERE filter | ❌ → WHERE filter |
| Hybrid | ✅ | ⚠️ Prefix only | ⚠️ DB only | ❌ |

Dialect is detected automatically from the database URL scheme. No manual
configuration needed.
