-- ─────────────────────────────────────────────────────────────────────────────
-- Bootstrap: tenant registry + public schema for shared data
-- ─────────────────────────────────────────────────────────────────────────────

-- Tenant registry lives in the public schema
CREATE TABLE IF NOT EXISTS tenants (
    id                 TEXT PRIMARY KEY,
    identifier         TEXT UNIQUE NOT NULL,
    name               TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'active',
    metadata           JSONB NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    database_url       TEXT,
    schema_name        TEXT,
    isolation_strategy TEXT
);

-- Seed demo tenants
INSERT INTO tenants (id, identifier, name, schema_name) VALUES
    ('tenant-acme-001',   'acme-corp', 'Acme Corporation', 'tenant_acme_corp'),
    ('tenant-globex-001', 'globex',    'Globex LLC',        'tenant_globex')
ON CONFLICT (id) DO NOTHING;

-- Per-tenant schemas are created by Alembic / SchemaIsolationProvider.initialize_tenant()
-- This init.sql only seeds the registry; schemas are bootstrapped at startup.
