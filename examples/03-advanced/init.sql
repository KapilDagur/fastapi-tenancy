-- ─────────────────────────────────────────────────────────────────────────────
-- Bootstrap: tenant registry, RLS for starter tier, schemas for enterprise
-- ─────────────────────────────────────────────────────────────────────────────

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

-- Enterprise tenants (schema isolation — premium tier)
INSERT INTO tenants (id, identifier, name, metadata, schema_name) VALUES
    ('tenant-acme-001',    'acme-corp',  'Acme Corporation',  '{"plan":"enterprise","max_projects":500}', 'tenant_acme_corp'),
    ('tenant-techcorp-001','tech-corp',  'TechCorp Inc',      '{"plan":"enterprise","max_projects":500}', 'tenant_tech_corp')
ON CONFLICT (id) DO NOTHING;

-- Starter tenants (RLS isolation — standard tier, shared schema)
INSERT INTO tenants (id, identifier, name, metadata) VALUES
    ('tenant-startup-001', 'startup-x', 'Startup X',          '{"plan":"starter","max_projects":10}'),
    ('tenant-dev-001',     'dev-labs',  'Dev Labs',            '{"plan":"starter","max_projects":10}')
ON CONFLICT (id) DO NOTHING;

-- ── Shared schema tables (used by RLS/starter tenants) ────────────────────────

-- Projects in the shared schema are filtered by RLS
CREATE TABLE IF NOT EXISTS projects (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_projects_tenant ON projects (tenant_id);

CREATE TABLE IF NOT EXISTS tasks (
    id          BIGSERIAL PRIMARY KEY,
    project_id  BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    title       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'todo',
    assignee    TEXT,
    due_date    TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tasks_tenant    ON tasks (tenant_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project   ON tasks (project_id);

-- ── RLS for shared schema ─────────────────────────────────────────────────────

ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks    ENABLE ROW LEVEL SECURITY;

CREATE POLICY project_tenant_isolation ON projects
    USING (tenant_id = current_setting('app.current_tenant', TRUE));

CREATE POLICY project_tenant_insert ON projects FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.current_tenant', TRUE));

CREATE POLICY task_tenant_isolation ON tasks
    USING (tenant_id = current_setting('app.current_tenant', TRUE));

CREATE POLICY task_tenant_insert ON tasks FOR INSERT
    WITH CHECK (tenant_id = current_setting('app.current_tenant', TRUE));
