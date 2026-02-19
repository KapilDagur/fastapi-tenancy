# Isolation Strategies

fastapi-tenancy supports four isolation strategies. Each maps to a different tradeoff between data security, operational overhead, and database support.

---

## Comparison

| Strategy | Isolation | Resource cost | PostgreSQL | SQLite (dev) | MySQL | MSSQL |
|----------|:---------:|:-------------:|:----------:|:------------:|:-----:|:-----:|
| Schema   | High      | Low–medium    | ✓ native   | ✓ prefix     | ✓ delegated | ✓ native |
| Database | Very high | High          | ✓          | ✓ file-per   | ✓     | ⚠ manual |
| RLS      | Medium    | Very low      | ✓ native   | ✓ filter     | ✓ filter | ✓ filter |
| Hybrid   | Tiered    | Tiered        | ✓          | ✓            | ✓     | —    |

---

## Schema isolation

Each tenant lives in its own PostgreSQL schema. Unqualified table references are resolved via `SET search_path = <tenant_schema>, public` on every session.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    isolation_strategy="schema",
    schema_prefix="t_",     # tenant "acme-corp" → schema "t_acme_corp"
)
```

### SQLite fallback (prefix mode)

On SQLite and other dialects without native schemas, the library automatically switches to **table-name prefixing**: every table is created as `t_<slug>_<table>` and `session.info["table_prefix"]` is set for your ORM event subscribers.

```python
# Same config works in development:
config = TenancyConfig(
    database_url="sqlite+aiosqlite:///./dev.db",
    isolation_strategy="schema",
)
provider = SchemaIsolationProvider(config)
prefix = provider.get_table_prefix(tenant)  # "t_acme_corp_"
```

No code changes are required when switching from SQLite dev to PostgreSQL production.

---

## Database isolation

Each tenant has a dedicated database (PostgreSQL) or `.db` file (SQLite).

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@host/master",
    isolation_strategy="database",
    # Optional: control the per-tenant URL pattern
    database_url_template="postgresql+asyncpg://user:pass@host/{database_name}",
)
```

MSSQL: database-per-tenant requires manual database creation; the provider raises a clear `IsolationError` with instructions.

---

## Row-Level Security (RLS)

All tenants share a single schema. PostgreSQL RLS policies enforce data boundaries server-side. The session variable `app.current_tenant` is set on every connection.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    isolation_strategy="rls",
)
```

Required one-time DDL per table:
```sql
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON orders
  USING (tenant_id = current_setting('app.current_tenant'));
```

Non-PostgreSQL dialects fall back to explicit `WHERE tenant_id = :id` via `apply_filters()`.

---

## Hybrid isolation

Mix strategies by tenant tier. Typically: premium tenants get schema isolation; standard tenants share via RLS.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    isolation_strategy="hybrid",
    premium_isolation_strategy="schema",
    standard_isolation_strategy="rls",
)
```

Mark a tenant as premium via its metadata or a custom `is_premium` attribute on `Tenant`.

---

## Choosing a strategy

| Situation | Recommended strategy |
|-----------|---------------------|
| Strict regulatory compliance (HIPAA, SOC 2) | `database` |
| Most SaaS products, balanced approach | `schema` |
| High tenant count (1000+), tight resources | `rls` |
| Multiple plan tiers | `hybrid` |
| Development / CI | any strategy + SQLite |
