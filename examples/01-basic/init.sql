-- ─────────────────────────────────────────────────────────────────────────────
-- Bootstrap: seed two demo tenants and enable RLS on the notes table
-- Runs automatically when the Postgres container starts for the first time.
-- ─────────────────────────────────────────────────────────────────────────────

-- Tenant registry (managed by fastapi-tenancy)
CREATE TABLE IF NOT EXISTS tenants (
    id          TEXT PRIMARY KEY,
    identifier  TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    database_url   TEXT,
    schema_name    TEXT,
    isolation_strategy TEXT
);

-- Seed tenants
INSERT INTO tenants (id, identifier, name) VALUES
    ('tenant-acme-001',   'acme-corp',    'Acme Corporation'),
    ('tenant-globex-001', 'globex',       'Globex LLC')
ON CONFLICT (id) DO NOTHING;

-- Notes table with tenant_id for RLS
CREATE TABLE IF NOT EXISTS notes (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast per-tenant lookups
CREATE INDEX IF NOT EXISTS idx_notes_tenant_id ON notes (tenant_id);

-- ── RLS ──────────────────────────────────────────────────────────────────────
ALTER TABLE notes ENABLE ROW LEVEL SECURITY;

-- Policy: each connection only sees rows for the current tenant.
-- fastapi-tenancy sets app.current_tenant before every query.
CREATE POLICY tenant_isolation ON notes
    USING (tenant_id = current_setting('app.current_tenant', TRUE));

-- Allow all operations when the setting matches
CREATE POLICY tenant_insert ON notes
    FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.current_tenant', TRUE));

-- ── Seed notes ────────────────────────────────────────────────────────────────
INSERT INTO notes (tenant_id, title, body) VALUES
    ('tenant-acme-001',   'Welcome to Notekeeper', 'This is your first note, Acme!'),
    ('tenant-acme-001',   'Q4 Goals',              'Launch new product line by December.'),
    ('tenant-globex-001', 'Welcome to Notekeeper', 'Hello from Globex!');
