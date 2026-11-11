-- Development-only database for docker-compose.standalone.yml.
-- These are the columns used by the collector's key, workspace, project and
-- subscription queries, NOT a replacement for the platform's Django migrations.
-- Apply only to the dedicated, empty standalone database. All identities are
-- UUIDs because the ClickHouse span schema stores UUID project/org IDs.
BEGIN;

CREATE TABLE accounts_workspace (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    is_default boolean NOT NULL,
    is_active boolean NOT NULL,
    deleted boolean NOT NULL
);
CREATE UNIQUE INDEX unique_default_workspace_per_org
    ON accounts_workspace (organization_id) WHERE is_default AND NOT deleted;

CREATE TABLE accounts_orgapikey (
    id uuid PRIMARY KEY,
    api_key varchar(255) NOT NULL UNIQUE,
    secret_key varchar(255) NOT NULL,
    organization_id uuid NOT NULL,
    workspace_id uuid REFERENCES accounts_workspace (id),
    user_id uuid,
    type varchar(50) NOT NULL,
    enabled boolean NOT NULL,
    deleted boolean NOT NULL
);

CREATE TABLE tracer_project (
    id uuid PRIMARY KEY,
    name varchar(255) NOT NULL,
    organization_id uuid NOT NULL,
    workspace_id uuid REFERENCES accounts_workspace (id),
    trace_type varchar(20) NOT NULL,
    model_type varchar(50) NOT NULL,
    source varchar(20) NOT NULL,
    tags jsonb NOT NULL,
    deleted boolean NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
-- Matches GetOrCreateProject's ON CONFLICT target, including its predicate.
CREATE UNIQUE INDEX unique_project_per_org_type
    ON tracer_project (name, trace_type, organization_id, workspace_id) WHERE NOT deleted;

CREATE TABLE usage_organizationsubscription (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    plan varchar(50) NOT NULL,
    tracing_billing_mode varchar(20) NOT NULL,
    deleted boolean NOT NULL
);

INSERT INTO accounts_workspace (id, organization_id, is_default, is_active, deleted)
VALUES ('00000000-0000-4000-8000-000000000002', '00000000-0000-4000-8000-000000000001', true, true, false);

-- Public local-test credentials only; never use these for a hosted deployment.
INSERT INTO accounts_orgapikey (id, api_key, secret_key, organization_id, workspace_id, user_id, type, enabled, deleted)
VALUES (
    '00000000-0000-4000-8000-000000000004',
    'standalone-api-key', 'standalone-secret-key',
    '00000000-0000-4000-8000-000000000001',
    '00000000-0000-4000-8000-000000000002',
    '00000000-0000-4000-8000-000000000003',
    'system', true, false
);

INSERT INTO usage_organizationsubscription (id, organization_id, plan, tracing_billing_mode, deleted)
VALUES ('00000000-0000-4000-8000-000000000005', '00000000-0000-4000-8000-000000000001', 'free', 'storage', false);

-- No project is pre-created: the first project_name is resolved and inserted by
-- the collector, with a generated UUID and the real conflict-handling query.
COMMIT;
