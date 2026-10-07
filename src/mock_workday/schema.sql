CREATE TABLE tenants (
    id uuid PRIMARY KEY,
    slug text NOT NULL UNIQUE,
    name text NOT NULL,
    enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE signing_keys (
    id uuid PRIMARY KEY,
    private_pem text NOT NULL,
    public_pem text NOT NULL,
    state text NOT NULL CHECK (state IN ('ACTIVE', 'VERIFY_ONLY', 'RETIRED')),
    created_at timestamptz NOT NULL
);
CREATE UNIQUE INDEX one_active_key ON signing_keys (state) WHERE state = 'ACTIVE';
CREATE TABLE tenant_config (
    tenant_id uuid PRIMARY KEY REFERENCES tenants,
    policy_version int NOT NULL DEFAULT 1
);

CREATE TABLE organizations (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    ref_id text NOT NULL,
    name text NOT NULL,
    superior_id uuid,
    UNIQUE (tenant_id, ref_id),
    FOREIGN KEY (tenant_id, superior_id) REFERENCES organizations (tenant_id, id),
    UNIQUE (tenant_id, id)
);

CREATE TABLE positions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    ref_id text NOT NULL,
    title text NOT NULL,
    org_id uuid NOT NULL,
    UNIQUE (tenant_id, ref_id),
    FOREIGN KEY (tenant_id, org_id) REFERENCES organizations (tenant_id, id),
    UNIQUE (tenant_id, id)
);

CREATE TABLE workers (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    employee_id text NOT NULL,
    name text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, employee_id),
    UNIQUE (tenant_id, id)
);

CREATE TABLE job_revisions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    worker_id uuid NOT NULL,
    position_id uuid NOT NULL,
    effective_date date NOT NULL,
    recorded_seq bigserial NOT NULL UNIQUE,
    recorded_at timestamptz NOT NULL,
    bp_event_id uuid,
    FOREIGN KEY (tenant_id, worker_id) REFERENCES workers (tenant_id, id),
    FOREIGN KEY (tenant_id, position_id) REFERENCES positions (tenant_id, id),
    UNIQUE (tenant_id, id)
);

CREATE TABLE compensation_revisions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    worker_id uuid NOT NULL,
    annual_salary numeric(12,2) NOT NULL,
    currency char(3) NOT NULL,
    effective_date date NOT NULL,
    recorded_seq bigserial NOT NULL UNIQUE,
    recorded_at timestamptz NOT NULL,
    bp_event_id uuid,
    FOREIGN KEY (tenant_id, worker_id) REFERENCES workers (tenant_id, id),
    UNIQUE (tenant_id, id)
);

CREATE TABLE accounts (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    username text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('HUMAN','ISU')),
    worker_id uuid,
    password_hash text NOT NULL,
    disabled boolean NOT NULL DEFAULT false,
    ui_sessions_allowed boolean NOT NULL,
    UNIQUE (tenant_id, username),
    FOREIGN KEY (tenant_id, worker_id) REFERENCES workers (tenant_id, id),
    CHECK ((kind = 'HUMAN' AND worker_id IS NOT NULL) OR (kind = 'ISU' AND worker_id IS NULL AND NOT ui_sessions_allowed)),
    UNIQUE (tenant_id, id)
);

CREATE TABLE role_assignments (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    role text NOT NULL CHECK (role IN ('MANAGER','HR_PARTNER','COMPENSATION_PARTNER')),
    org_id uuid NOT NULL,
    position_id uuid NOT NULL,
    assigned_at timestamptz NOT NULL,
    revoked_at timestamptz,
    FOREIGN KEY (tenant_id, org_id) REFERENCES organizations (tenant_id, id),
    FOREIGN KEY (tenant_id, position_id) REFERENCES positions (tenant_id, id),
    UNIQUE (tenant_id, id)
);

CREATE TABLE security_groups (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    name text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('SELF','ALL_EMPLOYEES','ROLE_BASED','INTEGRATION_UNCONSTRAINED','INTEGRATION_CONSTRAINED')),
    role text CHECK (role IN ('MANAGER','HR_PARTNER','COMPENSATION_PARTNER')),
    access_rights text,
    UNIQUE (tenant_id, name),
    UNIQUE (tenant_id, role),
    CHECK ((kind = 'ROLE_BASED' AND role IS NOT NULL AND access_rights IN ('CURRENT_ONLY','ALL_SUBORDINATES','UNASSIGNED_SUBORDINATES')) OR (kind <> 'ROLE_BASED' AND role IS NULL AND access_rights IS NULL)),
    UNIQUE (tenant_id, id)
);

CREATE TABLE api_clients (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    client_id text NOT NULL,
    secret_hash text NOT NULL,
    name text NOT NULL,
    scope_ceiling text[] NOT NULL,
    isu_account_id uuid,
    disabled boolean NOT NULL DEFAULT false,
    UNIQUE (tenant_id, client_id),
    FOREIGN KEY (tenant_id, isu_account_id) REFERENCES accounts (tenant_id, id),
    UNIQUE (tenant_id, id)
);

CREATE TABLE delegation_grants (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    user_account_id uuid NOT NULL,
    client_id text NOT NULL,
    scopes text[] NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    FOREIGN KEY (tenant_id, user_account_id) REFERENCES accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, client_id) REFERENCES api_clients (tenant_id, client_id),
    UNIQUE (tenant_id, id)
);

CREATE TABLE documents (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    title text NOT NULL,
    content text CHECK (octet_length(content) <= 65536),
    content_bytes integer CHECK (content_bytes BETWEEN 0 AND 65536),
    content_sha256 text CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (content IS NOT NULL OR (content_bytes IS NOT NULL AND content_sha256 IS NOT NULL)),
    domain text NOT NULL CHECK (domain IN ('DOC_TENANT','DOC_ORG','DOC_WORKER')),
    classification text NOT NULL CHECK (classification IN ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')),
    owner_worker_id uuid,
    org_id uuid,
    created_by_account_id uuid NOT NULL,
    created_by_client_id text,
    created_at timestamptz NOT NULL,
    FOREIGN KEY (tenant_id, owner_worker_id) REFERENCES workers (tenant_id, id),
    FOREIGN KEY (tenant_id, org_id) REFERENCES organizations (tenant_id, id),
    FOREIGN KEY (tenant_id, created_by_account_id) REFERENCES accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, created_by_client_id) REFERENCES api_clients (tenant_id, client_id),
    CHECK ((domain = 'DOC_TENANT' AND owner_worker_id IS NULL AND org_id IS NULL AND classification <> 'PUBLIC') OR (domain = 'DOC_ORG' AND org_id IS NOT NULL AND owner_worker_id IS NULL AND classification IN ('CONFIDENTIAL','RESTRICTED')) OR (domain = 'DOC_WORKER' AND owner_worker_id IS NOT NULL AND org_id IS NULL AND classification IN ('CONFIDENTIAL','RESTRICTED'))),
    UNIQUE (tenant_id, id)
);


CREATE TABLE integration_group_members (
    tenant_id uuid NOT NULL REFERENCES tenants,
    group_id uuid NOT NULL,
    account_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, group_id, account_id),
    FOREIGN KEY (tenant_id, group_id) REFERENCES security_groups (tenant_id, id),
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id)
);
CREATE TABLE integration_group_orgs (
    tenant_id uuid NOT NULL REFERENCES tenants,
    group_id uuid NOT NULL,
    org_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, group_id, org_id),
    FOREIGN KEY (tenant_id, group_id) REFERENCES security_groups (tenant_id, id),
    FOREIGN KEY (tenant_id, org_id) REFERENCES organizations (tenant_id, id)
);
CREATE TABLE domain_grants (
    tenant_id uuid NOT NULL REFERENCES tenants,
    domain text NOT NULL CHECK (domain IN ('WORKER_BASIC','WORKER_ORGANIZATIONS','WORKER_COMPENSATION','ABSENCE','DOC_TENANT','DOC_ORG','DOC_WORKER')),
    group_id uuid NOT NULL,
    permission text NOT NULL CHECK (permission IN ('VIEW','MODIFY')),
    PRIMARY KEY (tenant_id, domain, group_id),
    FOREIGN KEY (tenant_id, group_id) REFERENCES security_groups (tenant_id, id)
);
CREATE TABLE audit_authz (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    request_id text NOT NULL,
    account_id uuid,
    client_id text,
    grant_id uuid,
    action text NOT NULL,
    domain text,
    resource_type text NOT NULL,
    resource_id uuid,
    decision text NOT NULL,
    reason text NOT NULL,
    matched_group_id uuid,
    constraining_org_id uuid,
    job_revision_id uuid,
    policy_version int NOT NULL,
    at timestamptz NOT NULL
);
CREATE TABLE audit_objects (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    request_id text NOT NULL,
    account_id uuid,
    client_id text,
    object_type text NOT NULL,
    object_id uuid NOT NULL,
    field text NOT NULL,
    old_value jsonb,
    new_value jsonb,
    bp_event_id uuid,
    at timestamptz NOT NULL
);
CREATE INDEX jobs_as_of ON job_revisions (tenant_id,
     worker_id,
     effective_date DESC,
     recorded_seq DESC);
CREATE INDEX compensation_as_of ON compensation_revisions (tenant_id,
     worker_id,
     effective_date DESC,
     recorded_seq DESC);
GRANT USAGE ON SCHEMA public TO mw_app;
GRANT SELECT ON tenants, signing_keys TO mw_app;

CREATE TABLE bp_events (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    type text NOT NULL CHECK (type IN ('CHANGE_JOB', 'REQUEST_TIME_OFF')),
    subject_worker_id uuid NOT NULL,
    initiator_account_id uuid NOT NULL,
    initiator_client_id text,
    status text NOT NULL CHECK (status IN ('IN_PROGRESS', 'SUCCESSFULLY_COMPLETED', 'DENIED', 'CANCELED')),
    current_step int,
    effective_date date,
    payload jsonb NOT NULL,
    comment text NOT NULL,
    version int NOT NULL,
    initiated_at timestamptz NOT NULL,
    completed_at timestamptz,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, subject_worker_id) REFERENCES workers (tenant_id, id),
    FOREIGN KEY (tenant_id, initiator_account_id) REFERENCES accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, initiator_client_id) REFERENCES api_clients (tenant_id, client_id)
);
CREATE TABLE bp_steps (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    event_id uuid NOT NULL,
    step_order int NOT NULL,
    step_key text NOT NULL CHECK (step_key IN ('RECEIVING_MANAGER', 'COMPENSATION_PARTNER', 'MANAGER_APPROVAL')),
    status text NOT NULL CHECK (status IN ('PENDING', 'AWAITING', 'APPROVED', 'DENIED', 'SKIPPED', 'CANCELED')),
    initial_assignee_account_ids uuid[] NOT NULL,
    acted_by uuid,
    acted_by_client text,
    acted_at timestamptz,
    comment text,
    UNIQUE (tenant_id, event_id, step_order),
    FOREIGN KEY (tenant_id, event_id) REFERENCES bp_events (tenant_id, id),
    FOREIGN KEY (tenant_id, acted_by) REFERENCES accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, acted_by_client) REFERENCES api_clients (tenant_id, client_id)
);
CREATE TABLE bp_history (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    event_id uuid NOT NULL,
    action text NOT NULL,
    step_key text,
    actor_account_id uuid NOT NULL,
    actor_client_id text,
    comment text NOT NULL,
    at timestamptz NOT NULL,
    FOREIGN KEY (tenant_id, event_id) REFERENCES bp_events (tenant_id, id),
    FOREIGN KEY (tenant_id, actor_account_id) REFERENCES accounts (tenant_id, id),
    FOREIGN KEY (tenant_id, actor_client_id) REFERENCES api_clients (tenant_id, client_id)
);
CREATE TABLE idempotency_records (
    tenant_id uuid NOT NULL REFERENCES tenants,
    account_id uuid NOT NULL,
    client_key text NOT NULL,
    idem_key text NOT NULL CHECK (length(idem_key) BETWEEN 1 AND 128),
    request_hash text NOT NULL,
    operation_id uuid NOT NULL,
    status_code int NOT NULL,
    receipt jsonb NOT NULL,
    response jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, account_id, client_key, idem_key),
    FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id)
);
ALTER TABLE job_revisions ADD FOREIGN KEY (tenant_id, bp_event_id) REFERENCES bp_events (tenant_id, id);
ALTER TABLE compensation_revisions ADD FOREIGN KEY (tenant_id, bp_event_id) REFERENCES bp_events (tenant_id, id);
CREATE INDEX pending_change_subject ON bp_events (tenant_id, subject_worker_id) WHERE type='CHANGE_JOB';

ALTER TABLE tenant_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_config FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_config
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON tenant_config TO mw_app;

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON organizations
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON organizations TO mw_app;

ALTER TABLE positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE positions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON positions
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON positions TO mw_app;

ALTER TABLE workers ENABLE ROW LEVEL SECURITY;
ALTER TABLE workers FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workers
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON workers TO mw_app;

ALTER TABLE job_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON job_revisions
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON job_revisions TO mw_app;

ALTER TABLE compensation_revisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE compensation_revisions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON compensation_revisions
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON compensation_revisions TO mw_app;

ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON accounts
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON accounts TO mw_app;

ALTER TABLE role_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE role_assignments FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON role_assignments
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON role_assignments TO mw_app;

ALTER TABLE security_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_groups FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON security_groups
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON security_groups TO mw_app;

ALTER TABLE api_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_clients FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON api_clients
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON api_clients TO mw_app;

ALTER TABLE delegation_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE delegation_grants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delegation_grants
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON delegation_grants TO mw_app;
GRANT UPDATE (revoked_at) ON delegation_grants TO mw_app;

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON documents
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON documents TO mw_app;

ALTER TABLE integration_group_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration_group_members FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON integration_group_members
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON integration_group_members TO mw_app;

ALTER TABLE integration_group_orgs ENABLE ROW LEVEL SECURITY;
ALTER TABLE integration_group_orgs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON integration_group_orgs
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON integration_group_orgs TO mw_app;

ALTER TABLE domain_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE domain_grants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON domain_grants
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON domain_grants TO mw_app;

ALTER TABLE audit_authz ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_authz FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_authz
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON audit_authz TO mw_app;

ALTER TABLE audit_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_objects FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_objects
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON audit_objects TO mw_app;

GRANT USAGE ON SEQUENCE job_revisions_recorded_seq_seq, compensation_revisions_recorded_seq_seq TO mw_app;

ALTER TABLE bp_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE bp_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON bp_events
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON bp_events TO mw_app;
GRANT UPDATE (status, current_step, version, completed_at) ON bp_events TO mw_app;
ALTER TABLE bp_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE bp_steps FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON bp_steps
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON bp_steps TO mw_app;
GRANT UPDATE (status, acted_by, acted_by_client, acted_at, comment) ON bp_steps TO mw_app;
ALTER TABLE bp_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE bp_history FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON bp_history
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON bp_history TO mw_app;
ALTER TABLE idempotency_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON idempotency_records
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT, DELETE ON idempotency_records TO mw_app;

CREATE TABLE time_off_balances (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants,
    worker_id uuid NOT NULL,
    plan text NOT NULL CHECK (plan = 'VACATION'),
    as_of date NOT NULL,
    granted_hours numeric(8,2) NOT NULL CHECK (granted_hours >= 0 AND mod(granted_hours, 0.25) = 0),
    taken_hours numeric(8,2) NOT NULL CHECK (taken_hours >= 0 AND mod(taken_hours, 0.25) = 0),
    remaining_hours numeric(8,2) NOT NULL CHECK (remaining_hours = granted_hours - taken_hours AND remaining_hours >= 0),
    UNIQUE (tenant_id, worker_id, plan, as_of),
    FOREIGN KEY (tenant_id, worker_id) REFERENCES workers (tenant_id, id)
);
ALTER TABLE time_off_balances ENABLE ROW LEVEL SECURITY;
ALTER TABLE time_off_balances FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON time_off_balances
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT ON time_off_balances TO mw_app;

CREATE TABLE seed_loads (
    tenant_id uuid PRIMARY KEY REFERENCES tenants,
    version text NOT NULL,
    checksum text NOT NULL,
    complete boolean NOT NULL DEFAULT false
);
ALTER TABLE seed_loads ENABLE ROW LEVEL SECURITY;
ALTER TABLE seed_loads FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON seed_loads
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
