# ADR 0004 - Per-tenant database names derive from the tenant identifier

- **Status:** Accepted
- **Date:** 2026-07-28
- **Deciders:** fastapi-tenancy maintainers
- **Related:** review finding F1; breaking change to `TenancyConfig.get_database_url_for_tenant()`

## Context

Under `IsolationStrategy.DATABASE` every tenant gets its own database. Three
subsystems need that database's name, and each derived it independently:

| Subsystem | Location | Derived from |
| --- | --- | --- |
| `DatabaseIsolationProvider._database_name` - **creates** the database | `isolation/database.py` | `tenant.identifier` |
| `TenancyConfig.get_database_url_for_tenant` - builds connection URLs | `core/config.py` | `tenant.id` |
| `TenantMigrationManager._build_alembic_args` - **migrates** the database | `migrations/manager.py` | the config helper, so `tenant.id` |

The two derivations produced different names for the same tenant:

```python
# provider, from identifier "acme-corp"
f"tenant_{sanitize_identifier(tenant.identifier)}_db"   # tenant_acme_corp_db

# config, from id "tenant-Ab3xyz9mqp2s"
db_name = tenant_id.replace("-", "_").replace(".", "_").lower()
f"tenant_{db_name}_db"                                  # tenant_tenant_ab3xyz9mqp2s_db
```

`register_tenant` never populates `tenant.database_url`, so the fallback path is
the normal path, not an edge case. The provider provisioned
`tenant_acme_corp_db` and `upgrade_all` then pointed Alembic at
`tenant_tenant_ab3xyz9mqp2s_db`. Alembic migrated a non-existent or
auto-created empty database and reported success. **The tenant's real database
was never migrated, silently** - the failure surfaces later as missing tables at
runtime, far from its cause.

`BaseIsolationProvider.get_database_url()` used the same id-based derivation and
had no callers in `src/`, making it a trap for the first person to reach for it.

## Options considered

### Option A - Standardise on `tenant.id`

Change the provider to derive from `id` as well. Rejected on a correctness
property, not on taste.

`generate_tenant_id()` returns `f"{prefix}-{token_urlsafe(12)}"`.
`token_urlsafe` output is **case-sensitive** and draws from an alphabet
containing both `-` and `_`. The derivation lowercases and maps `-` to `_`, so
it is **not injective**: `"tenant-aB"` and `"tenant-Ab"` collapse to one name,
as do `"a-b"` and `"a_b"`. Two distinct tenants sharing one database is
cross-tenant data sharing - low probability, maximal blast radius.

### Option B - Standardise on `tenant.identifier` (chosen)

The identifier is validated by `validate_tenant_identifier` against
`^[a-z][a-z0-9\-]{1,61}[a-z0-9]$` before it is ever stored. It is already
lowercase and contains no `_`, so mapping `-` to `_` **is** injective over the
validated domain: distinct identifiers cannot collide. Identifiers are also
unique in the store, and the resulting name is legible to an operator running
`\l` - `tenant_acme_corp_db` rather than `tenant_tenant_ab3xyz9mqp2s_db`.

### Option C - Store the resolved name on the `Tenant` row

Add a `database_name` column written at provisioning time. This is the most
robust option - it survives even a future change to the naming rule, because
existing tenants keep the name they were created with.

Not chosen *now*: it requires a schema migration and a backfill for existing
deployments, and `Tenant.database_url` already provides a per-tenant override
for anyone who needs to pin an exact target. Recorded as a follow-up.

## Decision

`TenancyConfig.get_database_name(tenant_identifier)` is the **single source of
truth** for per-tenant database naming. It validates the identifier and raises
`ValueError` on anything that fails validation - it never sanitises silently.

The provider, `get_database_url_for_tenant()`,
`BaseIsolationProvider.get_database_url()`, and `TenantMigrationManager` all
resolve names through it. None of them re-implements the rule.

This forces a **breaking signature change**:

```python
# before
config.get_database_url_for_tenant(tenant.id)

# after
config.get_database_url_for_tenant(tenant.id, tenant.identifier)
```

Both arguments are genuinely required: the two template placeholders are fed
from different fields - `{tenant_id}` from the opaque ID, `{database_name}` from
the identifier. Making `tenant_identifier` optional would have preserved source
compatibility while silently keeping the broken derivation alive for any caller
that omitted it, which defeats the purpose.

## Consequences

### Positive

- The provisioning path and the migration path cannot disagree. They are the
  same function call.
- Distinct tenants cannot collide onto one database. This is now a structural
  property of the validated identifier grammar, not a probabilistic argument.
- Database names are legible in operator tooling.
- `BaseIsolationProvider.get_database_url()` is no longer a trap.

### Negative

- **Breaking for external callers** of `get_database_url_for_tenant()` and of
  `BaseIsolationProvider.get_database_url()`. Both are public. The change is
  loud - a `TypeError` at the call site, not a silent behaviour shift.
- **Existing DATABASE-isolation deployments provisioned before this change may
  hold databases under the old id-derived name.** Those tenants were, by
  definition, never successfully migrated by `upgrade_all`, so the affected
  databases are empty or partially provisioned. Operators should confirm which
  names exist before upgrading and set `Tenant.database_url` explicitly for any
  tenant whose real data lives under an old name.
- Renaming a tenant's identifier now implies renaming its database. The library
  does not do this automatically; a rename on a DATABASE-isolated tenant will
  point at a database that does not exist unless `database_url` is set.

### Neutral

- Per-tenant `Tenant.database_url` still overrides derivation entirely and is
  unaffected.
- SCHEMA isolation is untouched - `get_schema_name()` already derived from the
  identifier and was already the single source for schema names.

## Follow-ups

- Consider Option C (persist `database_name` on the tenant row) if the naming
  rule ever needs to change again, or to make identifier renames safe.
- Add an `e2e` test that provisions a real per-tenant PostgreSQL database and
  then migrates it end-to-end. The current regression test
  (`TestDatabaseNameAgreement`) pins that all four derivations agree, which is
  the defect that occurred; an e2e test would additionally catch template and
  URL-shape errors.
