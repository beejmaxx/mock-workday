# Mock Workday: detailed specification (v1)

**Status:** M1, M2 and D1 complete. M3 spec and slices 2a–2b approved; slice 2c implemented for review. Implements [the plan](mock-workday-plan-claude.md). Workday evidence is in [workday-verification.md](workday-verification.md).

**Labels:**

- **[WD]**: verified Workday behavior.
- **[WD-inspired]**: a simplified Workday concept.
- **[Lab]**: our own policy.

Unlabeled statements are implementation choices.

**Milestones:**

- **M1:** §0–4, §6 except §6.4, §7, §8, §9 except faults, §10 except the M2 seed addition, and the M1 tests in §11.
- **M2:** §5 (business processes), §6.4 (idempotency), fault injection in §9, the M2 seed addition, and the M2 tests in §11.
- **M3 slice 2a:** tenant storage and STS sessions, bulk-v1 seeding and balance snapshots.
- **M3 slice 2b:** ASU identity, credential verification, attribution and request logs (§3.6).
- **M3 slice 2c:** report exports (§6.7) and transactional business notifications (§5.7). Native AI and AWS infrastructure follow [m3-spec.md](m3-spec.md).

---

## 0. Runtime and project layout

| Item | Choice |
|---|---|
| Language | Python 3.12, managed with `uv` |
| Web | FastAPI on Uvicorn. Synchronous route handlers; database work is synchronous and transaction-scoped. |
| Database | PostgreSQL 16+ (17 locally) via SQLAlchemy 2 Core and psycopg 3 |
| Tokens | PyJWT with `cryptography`, RS256 |
| Tests | pytest. A session fixture creates a throwaway Postgres cluster (`initdb` into a temporary directory, `pg_ctl start` on a free port), so tests need no Docker. |
| Packaging | `Dockerfile` and `compose.yaml` for running the service and Postgres together. Container builds are not required to run tests. |
| Local workflow | `Makefile` targets: `make test` (throwaway Postgres, no Docker), `make up` (Compose: service + Postgres, schema + seed), `make down` (Compose down, volumes removed) |
| Schema | `schema.sql` applied by the owner role; no migration framework in v1 |

```text
mock-workday/
├── pyproject.toml
├── src/mock_workday/
│   ├── app.py            # FastAPI apps: public API and test-admin API
│   ├── config.py
│   ├── clock.py          # controllable clock
│   ├── db.py             # engine, tenant-scoped transactions
│   ├── schema.sql
│   ├── seed.py
│   ├── ids.py            # WID generation
│   ├── errors.py
│   ├── auth.py           # tokens, keys, principals, grants
│   ├── authz.py          # security groups, reach, decisions
│   ├── api/              # routers: oauth, workers, orgs, documents, bp, grants
│   ├── bp.py             # Change Job and Request Time Off (M2)
│   ├── reports.py        # bounded report snapshots and capability creation
│   ├── events.py         # transactional outbox dispatch and owner repair
│   ├── audit.py
│   ├── idempotency.py    # (M2)
│   ├── ratelimit.py
│   └── faults.py         # (M2)
└── tests/
```

**Processes:**

- One process serves two ASGI apps and one outbox dispatcher thread.
- **Public API:** container port 8080; Compose publishes to `127.0.0.1:${MW_PUBLIC_PORT:-8080}`.
- **Test-admin API:** container port 8081, enabled only when `MW_TEST_ADMIN=1`; Compose publishes to `127.0.0.1:${MW_ADMIN_PORT:-8081}`. It must never share the public port. Host-port overrides are Compose settings, not application configuration.

---

## 1. Tenancy

### Tenant resolution [Lab]

- The tenant comes from the `Host` header: `{slug}.mockworkday.local`, for example `acme.mockworkday.local`.
- An unknown host returns 404 `TENANT_NOT_FOUND`.
- A `tenant_id` or tenant slug in a request body or query is never read.
- Disabled tenants (including incomplete bulk imports) return 404 `TENANT_NOT_FOUND` before authentication or storage access. [Lab policy]

### Database roles and row-level security

- **Roles:**
  - `mw_owner` owns all tables, runs `schema.sql`, and runs seeding.
  - `mw_app` is used by the service. It is not an owner and has no `BYPASSRLS`.
- **Policies:** every tenant-scoped table has a `tenant_id` column and:

  ```sql
  ALTER TABLE t ENABLE ROW LEVEL SECURITY;
  ALTER TABLE t FORCE ROW LEVEL SECURITY;
  CREATE POLICY tenant_isolation ON t
    USING (tenant_id = current_setting('app.tenant_id')::uuid)
    WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
  ```

- **Explicit tenant predicates:** every query and mutation on tenant-scoped tables also filters by `tenant_id`; inserts carry it explicitly. RLS is the backstop, not the only isolation.
- **Per-transaction tenant context:** every request transaction begins with `SELECT set_config('app.tenant_id', :tid, true)`. The third argument (`true`) is equivalent to `SET LOCAL`, so the setting cannot leak through pooled connections. Code that needs a transaction must obtain it through `db.tenant_tx(tenant_id)`.
- **Global tables** (`tenants`, `signing_keys`) have no RLS. `mw_app` has `SELECT` on them; only `mw_owner` writes them.
- **Least privilege for `mw_app`:**
  - `SELECT` only on reference/configuration tables: `tenant_config`, `organizations`, `positions`, `workers`, `accounts`, `role_assignments`, `security_groups`, `api_clients`, `integration_group_members`, `integration_group_orgs`, `domain_grants`, and `time_off_balances`.
  - No app privileges on owner-only `seed_loads` import markers.
  - `SELECT, INSERT` on `job_revisions`, `compensation_revisions`, and `documents`; no `UPDATE` or `DELETE`. Revisions are append-only to preserve effective-dated history and stable pagination.
  - `SELECT, INSERT` plus column-level `UPDATE (revoked_at)` on `delegation_grants`; no other updates or deletes.
  - `SELECT, INSERT` on `bp_events` plus column-level `UPDATE (status, current_step, version, completed_at)`; on `bp_steps` plus `UPDATE (status, acted_by, acted_by_client, acted_at, comment)`. No deletes.
  - `SELECT, INSERT, DELETE` on `idempotency_records`; deletion is only for expired keys. No placeholder records or updates.
  - `INSERT, SELECT` only on audit tables (including `bp_history`); no `UPDATE` or `DELETE`.
  - `SELECT, INSERT` on `report_exports`; no app updates/deletes.
  - `SELECT, INSERT` on `event_outbox` plus `UPDATE (attempt_count,next_attempt_at,published_at,last_error)` only; no app detail updates/deletes.
  - `USAGE` only on the two revision sequences, for inserts.
- **Admin mutations** (role assignments, account/client disabling, policy-version changes, and reset) run as `mw_owner`, not `mw_app`.

---

## 2. Data model

**Conventions:**

- IDs are opaque WIDs: 32 lowercase hex characters [WD-inspired]. Stored as `uuid` and rendered as hex without dashes.
  - **Seed IDs are deterministic:** `uuid5(MW_NAMESPACE, f"{tenant_slug}:{object_type}:{ref}")`, where `MW_NAMESPACE` is a fixed UUID in code. Reseeding any environment reproduces the same IDs.
  - IDs created at runtime (events, documents, grants) are random `uuid4`.
- Reference IDs are human-readable and unique per tenant, for example employee ID `E1001` or organization reference `SO-ENG`.
- Time is `timestamptz` in UTC. Business dates are `date` values, interpreted in UTC [Lab].

### 2.1 Tables

```text
tenants(id uuid pk, slug text unique, name text, enabled bool default true)
tenant_config(tenant_id pk, policy_version int not null default 1)

organizations(id, tenant_id, ref_id, name, superior_id null → organizations)
    -- supervisory only; static in v1

positions(id, tenant_id, ref_id, title, org_id → organizations)
    -- static in v1

workers(id, tenant_id, employee_id, name, active bool)
    -- name and employee_id immutable in v1

job_revisions(id, tenant_id, worker_id, position_id, effective_date date,
              recorded_seq bigserial, recorded_at, bp_event_id null)
compensation_revisions(id, tenant_id, worker_id, annual_salary numeric(12,2),
              currency char(3), effective_date, recorded_seq, recorded_at,
              bp_event_id null)

accounts(id, tenant_id, username, kind HUMAN|ISU|ASU, worker_id null,
         password_hash, disabled bool, ui_sessions_allowed bool)
    -- HUMAN requires worker_id; ISU/ASU require worker_id null and ui_sessions_allowed=false

role_assignments(id, tenant_id, role MANAGER|HR_PARTNER|COMPENSATION_PARTNER,
                 org_id, position_id, assigned_at, revoked_at null)

security_groups(id, tenant_id, name, kind, role null, access_rights null)
    -- kind: SELF | ALL_EMPLOYEES | ROLE_BASED
    --       | INTEGRATION_UNCONSTRAINED | INTEGRATION_CONSTRAINED
    -- access_rights (ROLE_BASED only):
    --       CURRENT_ONLY | ALL_SUBORDINATES | UNASSIGNED_SUBORDINATES

integration_group_members(group_id, account_id, tenant_id)
integration_group_orgs(group_id, org_id, tenant_id)
    -- constrained groups: listed orgs plus all subordinates

domain_grants(tenant_id, domain, group_id, permission VIEW|MODIFY)

api_clients(id, tenant_id, client_id text, secret_hash, name,
            scope_ceiling text[], isu_account_id null, disabled bool,
            asu_id null, allowed_operations text[])
delegation_grants(id, tenant_id, user_account_id, client_id, scopes text[],
                  created_at, expires_at, revoked_at null)

documents(id, tenant_id, title, content text null, content_bytes int,
          content_sha256 text, domain, classification,
          owner_worker_id null, org_id null, created_by_account_id,
          created_by_client_id null, created_at)
    -- at most one of owner_worker_id, org_id is set; neither = tenant-wide

-- M3 slice 2a (Lab policy)
time_off_balances(id, tenant_id, worker_id, plan VACATION, as_of date,
                  granted_hours, taken_hours, remaining_hours)
    -- numeric(8,2), nonnegative quarter hours, remaining = granted - taken
    -- unique tenant/worker/plan/as_of; owner-only writes; FORCE RLS
seed_loads(tenant_id pk, version, checksum, complete bool default false)
    -- owner-only, FORCE RLS; partial bulk tenants are not routable

-- M3 slice 2b (Lab policy; constraints and custody in §3.6)
agent_registrations(id, tenant_id, ref_id, display_name, enabled, created_at)
agent_system_users(id, tenant_id, registration_id, account_id, client_id,
                   mode DELEGATE|AMBIENT, enabled, credential_store_ref)
credential_versions(id, tenant_id, asu_id, store_version, fingerprint null,
                    active_from, accept_until null, revoked_at null,
                    certificate_expires_at null)
assertion_uses(tenant_id, client_id, jti, expires_at)
audit_identity(id, tenant_id, request_id, action, operator null,
               by_user_account_id null, on_behalf_of_user_account_id null,
               agent_id null, asu_id null, credential_version_id null, at)

-- M3 slice 2c (Lab policy)
report_exports(id, tenant_id, account_id, client_id null, grant_id null,
               report, filters jsonb, row_count, byte_length, sha256,
               object_key, content bytea null, created_at, expires_at)
event_outbox(tenant_id, event_id → bp_history, schema_version, detail jsonb,
             created_at, attempt_count, next_attempt_at, published_at null,
             last_error null, pk(tenant_id,event_id))

-- M2
bp_events(id, tenant_id, type CHANGE_JOB|REQUEST_TIME_OFF, subject_worker_id,
          initiator_account_id, initiator_client_id null, status,
          current_step int null, effective_date date null, payload jsonb,
          comment text, version int, initiated_at, completed_at null)
bp_steps(id, tenant_id, event_id, step_order, step_key, status,
         initial_assignee_account_ids uuid[], acted_by null,
         acted_by_client null, acted_at null, comment null)
    -- status: PENDING | AWAITING | APPROVED | DENIED | SKIPPED | CANCELED
idempotency_records(tenant_id, account_id, client_key text, idem_key,
                    request_hash, operation_id, status_code, receipt jsonb,
                    response jsonb, created_at, expires_at,
                    pk(tenant_id, account_id, client_key, idem_key))
    -- client_key = client_id, or '-' for direct human tokens

-- audit, append-only
audit_authz(id, tenant_id, request_id, account_id, client_id, grant_id, action,
            domain, resource_type, resource_id, decision, reason,
            matched_group_id, constraining_org_id, job_revision_id,
            policy_version, at)
audit_objects(id, tenant_id, request_id, account_id, client_id, object_type,
              object_id, field, old_value jsonb, new_value jsonb, bp_event_id,
              at)
bp_history(id, tenant_id, event_id, action, step_key, actor_account_id,
           actor_client_id, comment, at,
           by_user_account_id null, on_behalf_of_user_account_id null,
           agent_id null, asu_id null, credential_version_id null, legacy_delegated,
           request_id null, business_process_version null, resulting_status null)
```

### 2.2 Effective-dated reads

- **Current job as of D:** the `job_revisions` row for the worker with the greatest `(effective_date, recorded_seq)` such that `effective_date <= D`.
- **Compensation:** resolved the same way.
- **No row as of D:** the worker did not exist yet on D, and is treated as not found.
- **Organization membership as of D:** the organization of the position in the worker's job as of D [WD]. A manager is a member of the organization containing their own position, never of the organization they manage, because the Manager role is an assignment, not membership.
- **Position occupancy invariant:** at most one worker holds a position as of any date. Enforced at Change Job completion (§5.3).

### 2.3 Classifications

Ordered: `PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED`.

---

## 3. Identity and tokens

### 3.1 Token endpoint: `POST /oauth2/token` (form-encoded)

| Grant | Input | Result |
|---|---|---|
| `password` [Lab shortcut for human login] | `username`, `password` | Human token: `typ=human`, `sub=account`, no client, unrestricted scopes |
| `client_credentials` [WD-inspired] | `client_id`, `client_secret`. The client must be bound to an ISU. | ISU token: `typ=isu`, `sub=ISU account`, `client_id`, `scope = scope_ceiling` |
| `urn:ietf:params:oauth:grant-type:token-exchange` [Lab] | `client_id`, `client_secret`, `grant_id` | Delegated token: `typ=delegated`, `sub=user`, `act={client_id}`, `gid`, `scope = grant.scopes ∩ client.scope_ceiling` |

**Failures:**

- Any authentication failure returns 401 `INVALID_CLIENT` or `INVALID_GRANT`. The response does not reveal which part failed.
- Disabled accounts and clients fail.
- A token-exchange grant must belong to that client and be active (not revoked, `expires_at > now`).
- The ISU account must not be disabled.

### 3.2 JWT format

- **Header:** `{"alg": "RS256", "kid": ..., "typ": "JWT"}`.
- **Claims:** `iss`, `aud`, `sub`, `typ`, `client_id?`, `act?`, `gid?`, `scope?`, `iat`, `exp`, `jti`.
  - `iss = https://{slug}.mockworkday.local`
  - `aud = https://{slug}.mockworkday.local/api`
- **Lifetime:** access tokens last 300 seconds by clock [Lab].
- **Keys:**
  - One issuer-wide set of RSA-2048 keys [Lab].
  - `GET /.well-known/jwks.json` serves every key that is not retired.
  - New tokens are signed by the newest `ACTIVE` key.
  - Rotation (admin) adds a new active key and marks the previous one `VERIFY_ONLY`; retiring removes it from JWKS.

### 3.3 Validation on every request (in order; any failure → 401 `UNAUTHENTICATED`)

1. A Bearer token is present and `alg == RS256` (allowlist).
2. `kid` is known and not retired; the signature is valid.
3. `iss` and `aud` equal the values for the request's tenant host. An Acme token on the Globex host fails here.
4. `exp > now >= iat - 5s`, by the controllable clock.
5. The account exists in this tenant and is not disabled.
6. If `client_id` is present, the client exists and is not disabled.
7. If `typ == delegated`, the grant `gid`:
   - exists;
   - belongs to `sub` and `client_id`;
   - is not revoked;
   - has `expires_at > now`.

   **This check runs on every request**, so revocation takes effect immediately.
8. **Effective scopes are recomputed on every request** from current state, never taken from the token alone:
   - delegated: `token.scope ∩ client.scope_ceiling (current) ∩ grant.scopes (current)`;
   - ISU: `token.scope ∩ client.scope_ceiling (current)`;
   - direct human: unrestricted (`None`).

The result is a `Principal`:

```python
@dataclass(frozen=True)
class Principal:
    tenant_id: UUID
    account_id: UUID
    kind: Literal["human", "isu", "delegated"]
    worker_id: UUID | None         # human and delegated
    client_id: str | None
    grant_id: UUID | None
    scopes: frozenset[str] | None  # effective scopes per §3.3 step 8; None = direct human
```

### 3.4 Delegation grants [Lab]

- `POST /api/v1/delegation-grants`, using a **direct human token only** (403 for delegated and ISU tokens).
  - Body: `{"client_id", "scopes": [...], "ttl_seconds": 3600}`.
  - `ttl_seconds` ranges from 60 to 86400; the default is 3600.
  - `scopes` must be a subset of the client's `scope_ceiling` (422 otherwise).
- `GET /api/v1/delegation-grants` lists the caller's grants.
- `DELETE /api/v1/delegation-grants/{id}` revokes the grant: sets `revoked_at` and returns 204. It is idempotent.

### 3.5 Scopes (functional areas) [WD-inspired]

| Scope | Domains and operations |
|---|---|
| `staffing` | WORKER_BASIC, WORKER_ORGANIZATIONS, Change Job (without compensation) |
| `compensation` | WORKER_COMPENSATION; required, in addition to `staffing`, for a Change Job that changes compensation |
| `absence` | ABSENCE, Request Time Off |
| `documents` | all document domains |

---

### 3.6 M3 ASU identity and credentials [Lab policy unless stated]

This section amends §2–4 and the legacy-only token rules above. The published
Workday identity model and primary sources are distinguished from this lab's
protocol choices in [M3 §2–3](m3-spec.md#2-agent-identities-and-the-existing-grant-a). The implementation
uses per-tenant registrations, at most one DELEGATE and one AMBIENT ASU per
registration, and one OAuth client per ASU. Registration and ASU start disabled.
ASU accounts have no worker, password login, or UI session. Existing HUMAN/ISU
clients and grants retain their meanings; bound ASU clients cannot use legacy
secret-only authentication as a fallback.

New FORCE-RLS tables are `agent_registrations`, `agent_system_users`,
`credential_versions`, `assertion_uses`, and append-only `audit_identity`.
Composite tenant foreign keys bind accounts, registrations, clients, versions
and audit attribution. App access to identity configuration is SELECT-only;
the owner performs audited administration. The app may insert issuance audit
and atomically consume assertion JTIs (and delete expired replay entries).
`api_clients` gains `asu_id` and `allowed_operations`; an empty operation list
denies all business API calls. Operation identifiers are the fixed existing
OpenAPI operationIds listed in `identity.OPERATIONS`, not arbitrary paths.

`POST /oauth2/token` adds these ASU forms (form-encoded input):

| Mode | Required input | Result |
|---|---|---|
| AMBIENT | `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`, `client_id`, `assertion` | `typ=ambient`, `sub=ASU account`, current integration-group authority |
| DELEGATE | Existing token-exchange grant type, `client_id`, `client_secret`, `credential_version_id`, `grant_id` | `typ=delegated`, `sub=human`, `act.client_id`, `act.sub=ASU account` |

Delegate renewal needs no fresh human subject token. Human presence is proven
once when creating the existing delegation grant; that grant remains the sole
consent/revocation record. Each call rechecks the current human account, grant,
client, registration, ASU and credential. Authority is the intersection of the
human's current authority, grant, client ceiling, issued scopes and current and
issued operation lists. Optional space-delimited `scope` can only narrow.
Ambient authority uses integration groups and denies every BP mutation and grant
creation. These ceilings are checked before idempotent receipt replay.

Ambient assertions require RS256 and the registered RSA (minimum 2048 bits)
X.509 public key, currently valid certificate, `kid=credential version UUID`,
`iss=client_id`, `sub=ASU username`, and exact canonical
`aud=https://{slug}.mockworkday.local/oauth2/token`. Host aliases do not change
this audience. Integer `iat`/`exp`, nonempty `jti` (maximum 128 characters),
`exp>now`, lifetime at most 60 seconds, and at most 5 seconds future `iat` are
required. `(tenant, client, jti)` is consumed atomically in the issuance
transaction, so a concurrent replay fails. Optional `nbf` must be satisfied.
Tokens carry agent/ASU/credential IDs and operation ceilings. Lifetime is at
most 300 seconds, capped by grant, credential acceptance and certificate expiry.

`MW_CREDENTIAL_STORE` is a private local JSON file path (default
`/tmp/mock-workday-credentials/credentials.json`) or `aws`. Local reads require
mode 0600 and current process ownership, reject symlinks, and updates use a
locked atomic replacement. AWS reads use the tenant-tagged cached STS session,
Secrets Manager reference and exact immutable version ID. Payloads bind tenant,
ASU and mode; the registry holds references/status only. The ambient private
key stays with the external signer. Delegate enrollment returns a random secret
once; only its scrypt verifier is stored. Missing/unavailable material fails
closed with 503; database status is rechecked on every call, with no positive
authorization cache.

Rotation accepts at most two current versions. Store the new material first,
then atomically activate its reference, audit, and cap the old version at
`now+300s`. Failed activation leaves the old version unchanged and may leave an
inert secret version for owner cleanup. Global public-key fingerprint uniqueness
rejects key reuse. Emergency revocation commits status/audit immediately and
invalidates already-issued tokens; local material is then removed best effort.
AWS obsolete versions await owner cleanup (staging labels never authorize).

Identity administration is available only on the existing isolated test-admin
listener when `MW_TEST_ADMIN=1`; never expose it through the public ALB. Its
[separate OpenAPI contract](admin-openapi.json) covers registration, enablement,
mode/client configuration, credential enrollment/activation and revocation.
Local enrollment generates delegate secrets. In AWS, the owner-only
`python -m mock_workday.credentials` command writes a prepared version and an
exclusive mode-0600 result file; the admin API activates `existing_version`.
The service's AWS role stays read-only for secrets. Terraform creates containers
and policies later, with no secret values in state.

`python -m mock_workday.identity` explicitly adds one deterministic disabled
registration and two disabled ASUs per existing tenant, without any credential.
It is idempotent and separate from the unchanged small/bulk fixtures. AWS mode
requires `--references` with a tenant-slug → mode → provisioned secret ARN map.
Fresh disposable schema installation is required; this is not an in-place
migration for a populated deployment.

Authorization/object audits and BP history gain `by_user_account_id`,
`on_behalf_of_user_account_id`, `agent_id`, `asu_id`,
`credential_version_id`, and `legacy_delegated`. For ASU delegation, By User is
the ASU and On Behalf Of is the human; ambient uses only By User. Legacy
unbound delegation is flagged without inventing an ASU. Successful issuance and
identity administration require append-only identity audit or fail closed.
Existing historical fixture rows may have null new attribution fields.

Application API requests emit structured JSON to stdout, including
`request_id` (the propagated `X-Request-Id`), tenant, method, route template,
status, duration and available actor identifiers. Unknown/preroute failures use
null route/duration where unavailable. Do not log raw paths, query strings,
headers, bodies, assertions, secrets or compensation. Unexpected handler errors
are sanitized to 503. Uvicorn access logging is disabled. Built-in documentation
and health-check OpenAPI responses are not business request audit records.
CloudWatch collection, operational KMS encryption and explicit **3-day** log
retention are checkpoint-3/4 infrastructure work, not a local-test assertion.

## 4. Authorization

### 4.1 Domains and permissions [WD-inspired]

- **Domains:**
  - `WORKER_BASIC`
  - `WORKER_ORGANIZATIONS`
  - `WORKER_COMPENSATION`
  - `ABSENCE`
  - `DOC_TENANT` (tenant-wide documents; minimum classification `INTERNAL`)
  - `DOC_ORG` (organization documents; minimum `CONFIDENTIAL`)
  - `DOC_WORKER` (worker documents; minimum `CONFIDENTIAL`)
- **Permissions:** every caller (human, delegated, or ISU) needs `VIEW` to read and `MODIFY` to write. `MODIFY` implies `VIEW`.
  - [Lab simplification] Workday separates View/Modify (tasks and reports) from Get/Put (integration operations) [WD]. Whether Workday's REST API checks Get/Put is not established, and the distinction adds nothing to the runtime problem, so the mock uses View/Modify for all REST callers.

### 4.2 Reach [WD semantics, Lab choices per group]

```python
def reach(role, anchor_org, access_rights, now) -> set[OrgId]:
    if access_rights == CURRENT_ONLY:
        return {anchor_org}
    if access_rights == ALL_SUBORDINATES:
        return {anchor_org} | descendants(anchor_org)
    # UNASSIGNED_SUBORDINATES: descend, but prune any sub-org (and its subtree)
    # where some *filled* position holds the same role directly, now.
    out, stack = {anchor_org}, children(anchor_org)
    while stack:
        o = stack.pop()
        if has_filled_direct_assignment(role, o, now):
            continue  # someone else covers o and everything below it
        out.add(o)
        stack.extend(children(o))
    return out
```

- A role assignment on a **vacant** position counts as unassigned: nobody holds it [WD: "don't have anyone with the specified assignable role"].
- Role assignments use current state only; they are not effective-dated [Lab].

### 4.3 Group membership relative to a target

**Targets:**

| Target kind | Organization |
|---|---|
| Worker | The worker's organization **as of now**, regardless of `as_of` [Lab: historical reads use current authority] |
| Organization document | `documents.org_id` |
| Worker document | The owner's organization as of now. `SELF` applies if the owner is the caller. |
| Tenant document | No organization. Only `ALL_EMPLOYEES` and unconstrained integration groups can match. |

```python
def memberships(p: Principal, target: Target, now) -> list[Membership]:
    m = []
    if p.kind in ("human", "delegated"):
        me = p.worker_id
        if not worker_active_now(me):
            return []
        m.append(Membership(ALL_EMPLOYEES))
        if target.worker_id == me:
            m.append(Membership(SELF))
        my_pos = position_as_of(me, now)
        for ra in active_assignments(position=my_pos):
            g = role_group(ra.role)              # one ROLE_BASED group per role
            if target.org and target.org in reach(ra.role, ra.org, g.access_rights, now):
                m.append(Membership(g, constraining_org=ra.org))
    else:  # isu
        for g in integration_groups(p.account_id):
            if g.kind == INTEGRATION_UNCONSTRAINED:
                m.append(Membership(g))
            elif target.org and target.org in union(
                {o} | descendants(o) for o in g.orgs
            ):
                m.append(Membership(g, constraining_org=...))
    return m
```

### 4.4 Decision

```python
def authorize(p, action: READ|WRITE, domain, target, now) -> Decision:
    need = VIEW if action == READ else MODIFY
    if p.scopes is not None and scope_for(domain) not in p.scopes:
        return deny("SCOPE")
    for mem in memberships(p, target, now):          # deterministic order
        if grant_satisfies(domain, mem.group, need):
            return allow(mem.group, mem.constraining_org)
    return deny("NO_GRANT")
```

- `Decision` carries the following, all recorded by audit:
  - `allowed`
  - `reason`
  - `matched_group_id`
  - `constraining_org_id`
  - `policy_version`
  - `job_revision_id` (the revision that placed the target in its organization)
- **Delegation intersection:** for a delegated principal, memberships come from the *user's* current state, and the scope check applies the grant and client ceiling. That yields user permissions ∩ client ceiling ∩ grant scopes [Lab].

### 4.5 Visibility and status codes [Lab]

| Situation | Response |
|---|---|
| Worker not found, in another tenant, or no `WORKER_BASIC` read | 404 `NOT_FOUND` |
| Worker visible, but sub-resource domain denied (compensation, absence) | 403 `FORBIDDEN` |
| Document without read | 404 |
| Business-process event not viewable (§5.6) | 404 |
| Write denied on a visible object | 403 |
| Lists | Unauthorized rows omitted silently, without denial audit records |

---

## 5. Business processes (M2)

### 5.1 Common rules [WD-inspired statuses; Lab rules]

**Statuses:**

```text
IN_PROGRESS → SUCCESSFULLY_COMPLETED | DENIED | CANCELED
```

**Actions:**

| Action | Rule |
|---|---|
| Approve | The current step's assignee; the event must be `IN_PROGRESS` |
| Deny | The current step's assignee; ends the event as `DENIED` |
| Cancel | The initiator account only, while `IN_PROGRESS` |

**Rules:**

- The actor's authority is evaluated at action time.
- **Check order for step actions:** event visible (404 otherwise), then `status == IN_PROGRESS` (409 `INVALID_STATE` otherwise), then `expected_step` and `expected_version` (409 `VERSION_CONFLICT`), then actor authorization (403).
- The subject can never approve or deny.
- The initiator can never approve.
- Delegated actors also need the scope for the process (§3.5).
- **Concurrency:** step actions carry `expected_step` and `expected_version`. A mismatch returns 409 `VERSION_CONFLICT`. Every transition increments `version`.
- **Global lock order:** idempotency key, event row (actions only), worker, destination position (Change Job initiation and final approval only). Never acquire in another order. Keep `SELECT ... FOR UPDATE` on `bp_events`. Other locks are transaction-scoped advisory locks: `pg_advisory_xact_lock(hashtextextended(key, 0))`, with keys `idem:<tenant>:<account>:<client_key>:<key>`, `worker:<tenant>:<wid>`, and `position:<tenant>:<pid>`. Hash collisions only over-serialize, which is acceptable. No row UPDATE grant is needed for worker or position locks.

### 5.2 Assignee resolution [Lab]

**`DIRECT_MANAGER(org)`:**

- The holder of a filled position with an active `MANAGER` assignment directly on `org`.
- If the holder is missing, is the subject, or is the initiator, move to `org.superior` and repeat.
- If none is found at the root, the step stalls; only cancel remains.

**`ROLE_WITH_REACH(role, org)`:** any holder of `role` whose reach (§4.2) includes `org`.

Routing (who should act) uses direct assignment for managers; security (who may see) uses reach. These are deliberately different.

### 5.3 Change Job (narrowed)

**Request:**

```json
POST /api/v1/business-processes/change-job
{"worker_id", "position_id", "effective_date",
 "compensation": {"annual_salary", "currency"} | null,
 "comment"}
```

**Initiation (one transaction):**

1. Acquire the worker advisory lock (after the idempotency lock).
2. **Authorize:**
   - The initiator is a member, via §4.3 against the subject, of a group allowed to initiate. Seed: Manager and HR Partner role groups.
   - Delegated callers need `staffing`, plus `compensation` if a compensation change is included.
3. **Validate:**
   - `effective_date >= today`.
   - The target position exists in this tenant and differs from the current position.
   - Acquire the destination-position advisory lock after the worker lock; under it, check that the position is vacant as of `effective_date` and no pending Change Job targets it.
   - Compensation, if present: salary greater than 0 (fits `numeric(12,2)`) and a valid ISO 4217 code from the frozen [SIX current list](https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml), retrieved 2026-10-07 [Lab validation policy].
4. **One pending Change Job per worker:** reject with 409 `PENDING_CHANGE_EXISTS` if another `CHANGE_JOB` for the subject is `IN_PROGRESS`, or `SUCCESSFULLY_COMPLETED` with `effective_date > today`.
5. Insert the event, steps, process history, and audit records.
6. Store the idempotency record (§6.4).

**Steps (seeded per tenant):**

| Order | Key | Assignee | Condition |
|---|---|---|---|
| 1 | `RECEIVING_MANAGER` | `DIRECT_MANAGER(target_position.org)` | always |
| 2 | `COMPENSATION_PARTNER` | `ROLE_WITH_REACH(COMPENSATION_PARTNER, target_position.org)` | compensation present; otherwise `SKIPPED` |

**Final approval (one transaction; follow the global lock order in §5.1):**

1. Check visibility, status, then version and step, in §5.1 order.
2. Check that the actor is the assignee (§5.2) at action time.
3. **Effective-date check:** if `effective_date < today`, return 409 `EFFECTIVE_DATE_PASSED` and change nothing. This check also applies to every non-final approval. Deny and cancel remain allowed.
4. Acquire the destination-position advisory lock after the worker lock; re-check vacancy as of `effective_date` and pending events under that lock.
5. Insert `job_revisions` (`effective_date`, `bp_event_id`) and, if present, `compensation_revisions`.
6. Set the status to `SUCCESSFULLY_COMPLETED` and `completed_at`.
7. Write history and object audit.
8. Commit.

No scheduler runs; revisions become current on their date [Lab].

### 5.4 Request Time Off

**Request:**

```json
POST /api/v1/business-processes/request-time-off
{"worker_id", "start_date", "end_date", "reason"}
```

**Rules:**

- **Initiation:** only the worker for themselves: the `SELF` membership, `ABSENCE` write. Delegated callers need `absence`.
- **Validation:** `start_date <= end_date`. Past dates are allowed [Lab: no effective-date rule for time off].
- **Single step:** `MANAGER_APPROVAL`, assigned to `DIRECT_MANAGER(subject's org as of now)`, resolved at action time. Alice approves before Bob's transfer takes effect; Priya approves after.
- **Completion:** sets `SUCCESSFULLY_COMPLETED`. No balances.
- **Cancel:** the subject, as initiator, may cancel while in progress.

### 5.5 Event visibility and field disclosure [Lab]

**Who may view an event:**

- the initiator;
- the subject;
- any initial assignee, current eligible assignee, or recorded actor on any step. Store initial assignee account IDs per step as an array (including all `ROLE_WITH_REACH` matches) for visibility only; actions always require current authority;
- principals whose memberships against the subject (now) match a group with read on the event's domain:
  - `WORKER_ORGANIZATIONS` for Change Job;
  - `ABSENCE` for Time Off.

**Field filtering:**

- `payload.compensation` and step comments on the compensation step are returned only to principals with `WORKER_COMPENSATION` read on the subject now, or to a current eligible assignee of an `AWAITING` compensation step, with the required scopes. Past participation grants event visibility only, never compensation disclosure. Priya's receiving-manager step therefore shows the move without the salary.
- Time Off `reason` is visible to the same set of principals as the event itself.

### 5.6 Event endpoints

- `GET /api/v1/business-process-events/{id}`
- `GET /api/v1/business-process-events?awaiting_me=true&status=&subject=&limit=&cursor=`
- `POST /api/v1/business-process-events/{id}/approve`
- `POST /api/v1/business-process-events/{id}/deny`
- `POST /api/v1/business-process-events/{id}/cancel`

Initiations return 201; actions return 200. Their body is `{operation_id, status, resource: ref, event}`. A replay always returns at least `{operation_id, status, resource: ref}`; stored event fields require current disclosure authorization.

Event responses contain the `bp_events` fields in §2.1 except `tenant_id`, plus `id`, `descriptor`, `href`, and `steps`. Each step contains `id`, `step_order`, `step_key`, `status`, `initial_assignee_account_ids`, `current_assignee_account_ids`, `acted_by`, `acted_by_client`, `acted_at`, and `comment` (subject to disclosure filtering). `expected_step` is the step key, not its numeric order.

The action endpoints take the body `{"expected_step", "expected_version", "comment"}` and require an `Idempotency-Key`.

---

### 5.7 Business notifications and transactional outbox (M3 slice 2c)

**[Workday-inspired]** Committed business-process transitions emit notifications.
**[Lab policy]** The versioned event contract is an explicit addition to the HTTP
integration boundary: source `lab.mock-workday`, detail type
`MockWorkday.BusinessEvent.v1`, and [detail JSON Schema](business-event-v1.schema.json).
HTTP remains the only source of authority to read or mutate business records.

**[Lab policy]** Emit `job_change.submitted` on initiation,
`job_change.approved` on final approval, and `time_off.approved` on final approval.
Intermediate approvals, denials, cancellations and seeded historical transitions
do not publish. Future-effective approval does not assert that a job is already
effective. History records gain original request ID, resulting status and BP
version. The immutable history row UUID is the stable notification `event_id`.
Detail contains schema version, tenant UUID/slug, event type, business-time
`occurred_at`, BP ID/version/status, worker ID, effective date (null for time
off), By User / On Behalf Of User, client ID, request ID and relative HTTP href.
No salary, reason, comment, document body, credential or signed URL is copied.
All fields are assembled from server state/history, not a caller's event body.

**[Lab policy]** Insert `event_outbox` in the same transaction as history,
revisions, audit, BP transition and idempotency receipt. Rollback leaves no
publishable event; an idempotent replay creates none. The row stores tenant,
stable event ID, schema version, immutable detail, wall-clock creation,
attempt count, next attempt, nullable publication time and sanitized error.
FORCE RLS and tenant predicates apply; JSON tenant/event/version must match the
row identity. The app can INSERT/SELECT and UPDATE only delivery bookkeeping;
it cannot change detail or delete rows.

**[Lab policy]** The executable service starts one synchronous dispatcher thread
in its existing image. Every second it scans enabled tenants and selects at most
10 due rows per tenant with `FOR UPDATE SKIP LOCKED`, holding the transaction
through publication/marking. This deliberately holds claimed row locks and a DB
connection across PutEvents, bounded by SDK retries/timeouts. It keeps claim and
acknowledgment simple in this small service; a lease-then-publish design would
reduce lock duration at larger scale but adds lease expiry and recovery logic.
The top-level dispatcher catches any Exception, logs only its class (never
message/traceback), and continues the next pass. SDK calls use bounded retries/timeouts. Each
`PutEvents` entry is inspected independently: only an entry with an EventId and
no error is marked published. Failed or unknown outcomes persist a generic
error and retry after 1, 2, 4, 8, 16, 32, then 60 seconds (capped). Logs expose
unpublished count and oldest age without details or remote error text.

**[Lab policy]** A crash after AWS acceptance and before DB commit republishes
with the same stable detail ID. Publication is at least once, conditional on
infrastructure recovery and retained intent; duplicate and out-of-order arrival
is normal. EventBridge envelope IDs can differ. Consumers deduplicate by
`(tenant_id,event_id)`, use BP versions to identify stale notifications, tolerate
version gaps, and refetch using current HTTP authority. An event carries no
authority by itself. [AWS PutEvents entry-result semantics](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-putevents.html)
also require deployment verification that the configured bus actually exists:
AWS may report success while dropping events addressed to a nonexistent bus.
That bus/rule/target verification remains a checkpoint-4 smoke requirement.

**[Lab policy]** `MW_EVENT_BUS_ARN` selects AWS publication using the service's
IAM role; absent means local capture, never AWS. Local dispatch marks rows
published and keeps the most recent 1,000 details in a process-local test capture;
PostgreSQL still retains intent/history. Tests call dispatch explicitly, so
seeding and app-factory construction start no background work or AWS calls.
There is no separate deployment, internal queue, consumer implementation or SNS.

**[Lab policy]** Owner-only `python -m mock_workday.events --tenant UUID republish
--start ISO_UTC --end ISO_UTC` repairs at most 100 history transitions in a
half-open range no wider than seven days. It reconstructs original details/IDs,
audits each selected notification, and atomically schedules outbox retry. Audit
failure rolls repair back. `prune` removes only rows published more than seven
days ago; unpublished rows never expire by age. History remains available to
repair after pruning. These are operator tools, not normal delivery.

**[Lab policy]** Cross-domain targets are approved external event buses, with
explicit tenant allowlists and one provider-owned standard SQS DLQ per target.
Receiver-side forwarding/SQS belongs to the other domain. Rule/role/resource
policies, 24-hour/185-attempt delivery retry, DLQ encryption/14-day retention and
alarms remain checkpoint-3/4 work described in M3 §6.3. The owner-only `redrive
--queue-url URL` command receives at most ten messages from a provider DLQ,
checks source/type/account/tenant/schema and exact committed history detail,
audits the attempt, and deletes a message only after confirmed republication.
Malformed, foreign or unknown outcomes remain for investigation/retry. A failed
delete can duplicate delivery; redrive may also reach other matching targets.
Provider DLQs cover delivery to the target bus, not downstream consumer failures.

## 6. API contract

### 6.1 Conventions

- **Base path:** `/api/v1` on the tenant host.
- **Request IDs:** `X-Request-Id` is optional; one is generated if absent and always echoed. It is recorded in audit and grants nothing.
- **Error body:**

  ```json
  {"error": {"code": "...", "message": "...", "request_id": "..."}}
  ```

- **References:**

  ```json
  {"id": "<wid>", "descriptor": "<display name>", "href": "/api/v1/<type>/<wid>"}
  ```

- **`as_of`:** an optional `YYYY-MM-DD` query parameter on reads; the default is today. It selects data only (§4.3).

### 6.2 Endpoints

| Method and path | Domain / permission | Milestone |
|---|---|---|
| `POST /oauth2/token` | — | M1 |
| `GET /.well-known/jwks.json` | — | M1 |
| `POST, GET /api/v1/delegation-grants`; `DELETE …/{id}` | direct human | M1 |
| `GET /api/v1/workers?org=&include_subordinates=&employee_id=&limit=&cursor=&as_of=` | WORKER_BASIC read per row | M1 |
| `GET /api/v1/workers/{wid}?as_of=` | WORKER_BASIC | M1 |
| `GET /api/v1/workers/{wid}/organizations?as_of=` | WORKER_ORGANIZATIONS | M1 |
| `GET /api/v1/workers/{wid}/compensation?as_of=` | WORKER_COMPENSATION (audited) | M1 |
| `GET /api/v1/workers/{wid}/time-off-balances?as_of=` | ABSENCE (audited), §6.2.1 | M3 2a |
| `GET /api/v1/workers/{wid}/history` | revisions; compensation fields only with WORKER_COMPENSATION | M1 |
| `GET /api/v1/workers/{wid}/direct-reports?as_of=` | WORKER_BASIC per row | M1 |
| `GET /api/v1/organizations/{wid}` and `…/{wid}/workers` | organizations: any authenticated tenant principal [Lab]; workers: per row | M1 |
| `GET /api/v1/positions/{wid}` | any authenticated tenant principal, like organizations [Lab] | M1 |
| `GET /api/v1/documents?owner_worker_id=&org_id=&limit=&cursor=` | document domain per row | M1 |
| `GET /api/v1/documents/{wid}` | document domain (audited if ≥ CONFIDENTIAL) | M1 |
| `POST /api/v1/documents` | document domain write, relative to the target | M1 |
| `POST /api/v1/business-processes/change-job` | §5.3 | M2 |
| `POST /api/v1/business-processes/request-time-off` | §5.4 | M2 |
| `/api/v1/business-process-events…` | §5.6 | M2 |

**Shapes:**

- **Worker:**

  ```json
  {id, descriptor, href, employeeId, active,
   primaryPosition: ref, primarySupervisoryOrganization: ref}
  ```

- **Compensation:**

  ```json
  {worker: ref, annualSalary, currency, effectiveDate}
  ```

- **History:**

  ```json
  [{type: "JOB"|"COMPENSATION", effectiveDate, recordedAt,
    position?: ref, annualSalary?, currency?, businessProcessEvent?: ref}]
  ```

- **Organization:** `{id, descriptor, href, refId, superior: ref | null}`.
- **Position:** `{id, descriptor, href, refId, organization: ref}`.
- **Worker organizations:** the primary supervisory organization reference.
- **Document metadata:** `{id, descriptor, href, title, domain, classification, owner: worker_ref | null, org: org_ref | null, created_at}`.
  - Lists return metadata only. They never return `content`.
  - `GET /documents/{id}` returns metadata plus `content`; confidential/restricted content reads are audited per §8.
  - `POST /documents` takes `{title, content, domain, classification, owner_worker_id?, org_id?}` and returns **201** with document metadata. Content is returned only by the single-document GET.
- **Delegation grant:** `{id, client_id, scopes, created_at, expires_at, revoked_at: timestamp | null}`.
  - Creation returns **201**; listing returns an array of the caller's grants.

**Direct reports [WD-inspired]:** workers whose organization (as of `as_of`) has a filled position holding `MANAGER` directly that belongs to `{wid}`, plus the managers of those organizations' immediate sub-organizations.

### 6.2.1 Time-off balance snapshots (M3 slice 2a) [Workday-inspired]

`GET /api/v1/workers/{wid}/time-off-balances?as_of=YYYY-MM-DD` requires current
ABSENCE VIEW and the `absence` scope for scoped callers. Apply current worker
reach even for historical dates. Unknown/hidden workers return 404. Return
`{"worker": Reference, "data": [{"plan":"VACATION", "asOf":"YYYY-MM-DD",
"grantedHours":160, "takenHours":8, "remainingHours":152}]}` using the latest
snapshot per plan no later than `as_of` (default current business date); no
snapshot means an empty `data`. Reads are sensitive/audited. Plans and dates
are snapshots, not a live accrual calculation. Existing Request Time Off
continues to make no balance changes. No balance write endpoint is added.

---

### 6.3 Pagination [Lab, decided]

- **Order:** keyset on `id` (the WID) ascending.
- **Request:** `limit` defaults to 50, with a maximum of 200.
- **Response:**

  ```json
  {"data": [...], "next_cursor": "..." | null}
  ```

- **Cursor:** opaque base64url of HMAC-signed JSON `{last_id, account_id, client_key, tenant_id, query_hash, exp}`.
  - `query_hash` covers path, filters, and `as_of`.
  - A mismatched, tampered, or expired cursor (more than 15 minutes) returns 400 `INVALID_CURSOR`.
- **Guarantees:**
  - Never returns duplicates.
  - Complete when data and authorization do not change.
  - Rows entering the set behind the cursor may be missed.
  - Each page is authorized at request time.
  - A page may be shorter than `limit` even when more rows follow, because unauthorized rows are filtered out. `next_cursor == null` is the only end signal.

### 6.4 Idempotency (M2) [Lab, decided]

- **Scope:** required (`Idempotency-Key`, at most 128 characters) on `POST` to business processes and event actions. A missing key returns 400. `POST /documents` does not use idempotency in v1.
- **Processing order (one transaction):**
  1. Authenticate (§3.3). A revoked grant or disabled client returns 401, even for a replay.
  2. Compute `request_hash = sha256(method, path, canonical JSON body)`.
  3. Acquire the transaction-scoped idempotency advisory lock (§5.1), then read `(tenant, account, client_key, key)`, ignoring expired records.
     - **Exists with a different hash:** 422 `IDEMPOTENCY_KEY_REUSED`.
     - **Exists:**
       - Return the original status code with header `Idempotent-Replay: true` and the stored **receipt** `{operation_id, status, resource: ref}`.
       - The receipt is returned without re-running business authorization; it is never executed again.
       - Any additional stored response fields are included only if the caller's current read authorization permits them (for example, a compensation value).
     - **Absent:**
       - Delete any expired record with this binding. Perform the operation.
       - Insert the completed record and receipt at the end of the transaction while holding the key lock; no placeholder and no UPDATE. A concurrent duplicate blocks on the key lock, then replays. The primary key remains a backstop.
       - Commit atomically with the operation.
- **Only successful operations are recorded.** A failed attempt changed nothing, so a retry re-evaluates.
- **Retention:** 24 hours by clock. An expired key is treated as new.

### 6.5 Rate limiting

- A token bucket per `(tenant, client_id)`, or `(tenant, account)` for direct human tokens.
- In-process state driven by the controllable clock, so it is deterministic. With `MW_TEST_ADMIN=1` (including tests), time moves only through set/advance. Otherwise it progresses in real time from the seed clock start.
- **Defaults:** capacity 100, refill 50 per second.
- The test admin can override limits per client.
- **Exhaustion:** 429 `RATE_LIMITED` with `Retry-After: ceil(seconds until one token)`.
- Runs after authentication, before authorization.
- **Limitation:** single-process state only.

### 6.6 Error codes

| HTTP | Code |
|---|---|
| 400 | `INVALID_CURSOR`, `IDEMPOTENCY_KEY_REQUIRED`, `BAD_REQUEST` |
| 401 | `UNAUTHENTICATED`, `INVALID_CLIENT`, `INVALID_GRANT` |
| 403 | `FORBIDDEN` |
| 404 | `NOT_FOUND`, `TENANT_NOT_FOUND` |
| 409 | `VERSION_CONFLICT`, `PENDING_CHANGE_EXISTS`, `EFFECTIVE_DATE_PASSED`, `POSITION_OCCUPIED`, `INVALID_STATE` |
| 422 | `VALIDATION_ERROR`, `IDEMPOTENCY_KEY_REUSED`, `REPORT_TOO_LARGE` |
| 429 | `RATE_LIMITED` |
| 503 | `AUDIT_UNAVAILABLE`, `SERVICE_UNAVAILABLE` (dependency failure or injected status fault) |
| 504 | `SIMULATED_LOST_RESPONSE` (fault injection only) |

---

### 6.7 Report exports (M3 slice 2c)

**[Workday-inspired]** `POST /api/v1/report-exports` accepts
`{report: "worker-roster", as_of?: "YYYY-MM-DD", include_compensation?: false}`.
Unknown reports/fields fail 422. This is one fixed synchronous report; no SQL,
report jobs or document bodies. **[Lab policy]** The handler uses a PostgreSQL
REPEATABLE READ snapshot, existing current-authority row checks, and requested
as-of job/compensation revisions. It writes UTF-8 NDJSON to a private temporary
file. Each line has the existing `Worker` response fields and, only when
requested and authorized for that row, a `compensation` object with the existing
`Compensation` response shape. Hidden workers are omitted. Unauthorized or
missing compensation fields are absent; no fabricated values or null salaries.

**[Lab policy]** Scoped clients need `staffing`, and `compensation` when requested.
ASUs also need `create_export_api_v1_report_exports_post` in both issued and
current operation ceilings. Ambient clients may export within their existing
read authority. Direct humans retain their ordinary permissions. Limits are
10,000 visible rows and 16 MiB encoded bytes including line endings; overflow
returns 422 `REPORT_TOO_LARGE` without a successful snapshot or capability.
Creation is a sensitive disclosure: authorization and object audit must commit
before the response releases a URL. Authorization is evaluated in the report's
snapshot; this is not a continuously reauthorized stream. Retries are new
snapshots, with no idempotency guarantee.

**[Lab policy]** Success is 201 with `{id, report, row_count, byte_length, sha256,
created_at, expires_at, download_url}` and `Cache-Control: no-store`. Both report
timestamps are wall-clock UTC, independent of the controllable business clock.
The URL lasts at most 60 seconds, rounded down and capped by remaining token,
grant, credential acceptance/certificate and signing STS-session lifetimes.
Elapsed wall time during construction also consumes the original authority
budget when business time is frozen. AWS `expires_at` is read from the generated
SigV4 signature. If no usable lifetime remains, creation fails without a URL.

**[Lab policy]** AWS writes an immutable, nonversioned S3 object at
`tenants/{tenant UUID}/exports/{export UUID}` using the tenant-tagged session,
that tenant's CMK, SSE-KMS and Bucket Keys. The returned regional HTTPS URL
allows GET of that exact object only. Single bounded `PutObject` keeps the
16-MiB lab transfer synchronous, avoids multipart leftovers, and uses
`IfNoneMatch=*`. Failed upload/audit rolls back DB metadata and attempts object
cleanup; unknown commit outcomes retain the object for lifecycle cleanup.
Creation audit records filters, counts, checksum, object ID and actor/grant,
never report bytes or a signed URL.

**[Lab policy — explicit storage-boundary exception]** Report bytes may be
fetched directly from S3 with the API-issued URL; documents remain API-only.
A presigned URL is a bearer capability: anyone holding it may reuse it until
expiry. Grant/role revocation does not instantly revoke an existing URL.
Deleting the object or denying S3 access can block subsequent requests; an
in-flight download may continue. Runtime networking must permit ordinary HTTPS
to S3; report downloads do not traverse Mock Workday PrivateLink. No shared S3
permissions or renewal/list endpoint are provided. See [AWS presigned URL
semantics](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).

**[Lab policy]** Local mode stores the bounded bytes in `report_exports.content`
and returns `GET /api/v1/report-exports/{id}/download?expires=...&signature=...`.
This capability requires no bearer header and is HMAC-bound to tenant, object,
expiry and GET. It checks the stored expiry and checksum, supports reuse, and
returns 404 on an invalid/expired capability. It does not recheck the originating
grant. The process-local signing secret makes restart invalidate outstanding
local URLs; local tenant disabling also blocks downloads. Downloads echo
`X-Request-Id` and use `no-store`. Logs contain route templates, never query
signatures. In AWS this local download route returns 404.

**[Lab policy]** `report_exports` stores tenant, owner account/client/grant,
report/filters, counts/checksum, object key, optional local bytes, and wall-clock
creation/expiry. FORCE RLS, tenant predicates and composite foreign keys apply;
app access is SELECT/INSERT only. Owner-only `python -m mock_workday.reports
--tenant UUID` clears local bodies older than one day, retaining disclosure
metadata. Terraform checkpoint 3 adds a one-day S3 lifecycle backstop for export
prefixes (asynchronous storage cleanup, not access TTL); teardown empties them.

## 7. Documents

| Domain | Target | View | Modify (create) | Minimum classification |
|---|---|---|---|---|
| `DOC_TENANT` | none | ALL_EMPLOYEES | none in v1 (seed only) | INTERNAL |
| `DOC_ORG` | `org_id` | Manager and HR Partner with reach | HR Partner with reach | CONFIDENTIAL |
| `DOC_WORKER` | `owner_worker_id` | SELF, Manager and HR Partner with reach | Manager and HR Partner with reach | CONFIDENTIAL |

**Creation rules [Lab, decided]:**

- The caller needs write on the domain relative to the target.
- The domain determines the target: `org_id` for `DOC_ORG`, `owner_worker_id` for `DOC_WORKER`. A mismatch returns 422.
- `classification >= domain minimum`, otherwise 422.
- Content is text only, at most 64 KB.

**Exfiltration test:** Carol (HR Partner) can read Bob's compensation. She writes it into a `DOC_ORG` document on Engineering, which Alice (Manager) can read. Mock Workday **allows this**, because each step is individually authorized. Preventing it is the runtime's problem.

---

### 7.1 Document storage (M3 slice 2a) [Lab policy]

Local mode retains UTF-8 bodies in PostgreSQL. In AWS mode, PostgreSQL holds
metadata, byte length and SHA-256 only; bodies are immutable S3 objects at
`tenants/{tenant UUID}/documents/{document UUID}` (hyphenated UUIDs). Paths and
buckets are server-derived. Document metadata/content response shapes, the
64-KiB UTF-8 limit and authorization stay unchanged. Lists never fetch bodies.
Authorize before S3; commit sensitive-read audit before responding. Missing,
corrupt or unavailable objects/keys return 503 `SERVICE_UNAVAILABLE`; never
fall back to a DB body in AWS mode. Audit object records include the content
fingerprint, not a second body copy.

Create writes S3 before metadata/audit commit, using a conditional immutable
put with SSE-KMS, the configured tenant CMK, and S3 Bucket Keys. On DB/audit
rollback, attempt object deletion; log tenant/document IDs on cleanup failure.
A response timeout after commit or an uncertain commit outcome must not delete
the object. Process crashes or
ambiguous upload failures may leave inaccessible objects: owner inventory and
bounded owner cleanup compares document IDs with committed metadata, scans at
most 1,000 keys per call and only removes objects older than 24 hours. It
serializes against bulk imports and provides a continuation token. This is not a
distributed transaction.

`MW_TENANT_STORAGE` is JSON keyed by hyphenated tenant WID, each value exactly
`{"bucket":"...","kms_key_id":"..."}`. Absent/empty mapping means local DB
storage. AWS mode also requires `MW_TENANT_DATA_ROLE_ARN`; a missing tenant
mapping fails closed. No caller can supply the role, tenant tag, bucket or key.
Cloud calls use synchronous boto3 and the standard AWS credential/Region chain.
The task role assumes the tenant data role with `tenant=<WID>`, duration 900s;
cache clients in memory by `(role ARN, tenant WID)`, refreshing under a per-tenant
lock with 120s remaining. Check enabled/provisioned tenant before cache use;
no fallback to base-role S3 credentials. Bounded SDK retries/timeouts apply.

The approved checkpoint-3 policy design is one bucket/CMK per tenant, tag-based
prefix/bucket/key restrictions, `sts:TagSession` trust, and no base-role tenant
data access; see [M3 §4.1](m3-spec.md#41-storage-and-isolation).
Slice 2a implements session tags and S3 request behavior; actual IAM/KMS
policy enforcement, Bucket Key/CloudTrail evidence and crypto-shredding require
checkpoint-3 planning and checkpoint-4 live validation. These are lab choices,
not claims about Workday infrastructure.

## 8. Audit

| Record | Written when | Transaction |
|---|---|---|
| `audit_authz` | Every sensitive read (balance snapshots; compensation; history with compensation fields; single-document content reads ≥ CONFIDENTIAL; document lists return metadata only), every write decision, every direct-request denial | Allowed sensitive reads and writes: same transaction, written **before** returning data. If the write fails, roll back and return 503 `AUDIT_UNAVAILABLE` [Lab experiment]. Denials: separate best-effort transaction; a failure is logged and the response stays a denial. |
| `audit_objects` | Every inserted revision, document, event status change, grant creation or revocation | Same transaction as the change |
| `bp_history` | Every initiate, approve, deny, or cancel action | Same transaction |

- Non-sensitive allowed reads (basic worker data, organizations, positions) are not audited in v1.
- Rows filtered out of worker, document, or event lists are not denials and write no `audit_authz` records. List membership uses a non-auditing authorization check; a direct request denied access still produces a denial record.
- Tests read audit tables directly. An audit API is deferred until the runtime needs correlation.

---

## 9. Test admin and failure injection

The test-admin API runs only with `MW_TEST_ADMIN=1`, on its own port. Its endpoints identify the tenant by `slug` in the body.

| Endpoint | Effect | Milestone |
|---|---|---|
| `POST /admin/clock {"now": iso8601}` / `{"advance_seconds": n}` | Set or advance the clock | M1 |
| `POST /admin/reset` | Truncate and reseed | M1 |
| `POST /admin/role-assignments` / `…/{id}/revoke` | Assign or revoke roles; increments `policy_version` | M1 |
| `POST /admin/accounts/{id}/disable`, `/admin/api-clients/{id}/disable` | Disable an account or client | M1 |
| `POST /admin/signing-keys/rotate`, `/admin/signing-keys/{kid}/retire` | Rotate or retire keys | M1 |
| `POST /admin/rate-limits {"slug", "client_id", "capacity", "refill_per_second"}` | Override rate limits | M1 |
| `POST /admin/faults` / `DELETE /admin/faults` | Add or clear faults | M2 |

**Fault rules (M2):**

- A fault has the shape `{"slug", "match": {"method", "path_prefix", "client_id"?}, "type", "value"?, "count": n}`.
- Types:
  - `latency` (value in milliseconds)
  - `status` (value is 429 or 503)
  - `timeout_before_commit`: the operation rolls back and the response is 504
  - `timeout_after_commit`: the operation commits and the response is 504 `SIMULATED_LOST_RESPONSE`
  - `audit_write_failure`: the next audit insert raises

Faults apply to matching requests until `count` is exhausted.

---

## 10. Seed dataset (synthetic)

**Clock start:** `2026-10-07T09:00:00Z`.

### Acme organizations and positions

| Org (ref) | Superior | Positions (ref: title → holder) |
|---|---|---|
| Office of the CEO (`SO-ROOT`) | — | `P-CEO`: CEO → Dana |
| Executive (`SO-EXEC`) | Office of the CEO | `P-ENG-DIR`: Engineering Director → Alice; `P-FIN-DIR`: Finance Director → Priya |
| Engineering (`SO-ENG`) | Executive | `P-ENG-1`: Engineer → Bob; `P-PLAT-MGR`: Platform Manager → Frank; `P-ENG-2`: Engineer (vacant) |
| Platform (`SO-PLAT`) | Engineering | `P-PLAT-1`: Engineer → Grace |
| Finance (`SO-FIN`) | Executive | `P-FIN-1`: Analyst (vacant) |
| HR (`SO-HR`) | Executive | `P-HRBP-1` → Carol; `P-HRBP-2` → Henry; `P-COMP-1` → Connie |

- Every manager sits in the organization above the one they manage [WD]: Dana in Office of the CEO manages Executive; Alice and Priya in Executive manage Engineering and Finance; Frank in Engineering manages Platform.
- `SO-ROOT` and HR have no Manager assignment. Dana reaches HR through Executive. `DIRECT_MANAGER` for HR workers resolves upward to Dana; for Dana it finds nobody, so a time-off request by Dana stalls (only cancel remains).

### Role assignments (role @ org ← position)

| Role | Assignments |
|---|---|
| MANAGER | Executive ← `P-CEO`; Engineering ← `P-ENG-DIR`; Platform ← `P-PLAT-MGR`; Finance ← `P-FIN-DIR` |
| HR_PARTNER | Engineering ← `P-HRBP-1`; Finance ← `P-HRBP-1`; Platform ← `P-HRBP-2` |
| COMPENSATION_PARTNER | Engineering ← `P-COMP-1`; Finance ← `P-COMP-1` |

### Security groups

| Group | Kind | Access rights |
|---|---|---|
| Manager | ROLE_BASED | ALL_SUBORDINATES |
| HR Partner | ROLE_BASED | UNASSIGNED_SUBORDINATES |
| Compensation Partner | ROLE_BASED | ALL_SUBORDINATES |
| Employee as Self | SELF | — |
| All Employees | ALL_EMPLOYEES | — |
| Integration: Directory Reader | INTEGRATION_UNCONSTRAINED | — |
| Integration: Engineering Reader | INTEGRATION_CONSTRAINED on Engineering | — |

### Domain grants

| Domain | Grants |
|---|---|
| WORKER_BASIC | Self VIEW; Manager VIEW; HR Partner MODIFY; Compensation Partner VIEW; Directory Reader VIEW; Engineering Reader VIEW |
| WORKER_ORGANIZATIONS | Self VIEW; Manager VIEW; HR Partner MODIFY; Directory Reader VIEW |
| WORKER_COMPENSATION | Self VIEW; HR Partner VIEW; Compensation Partner MODIFY |
| ABSENCE | Self MODIFY; Manager VIEW; HR Partner VIEW |
| DOC_TENANT | All Employees VIEW |
| DOC_ORG | Manager VIEW; HR Partner MODIFY |
| DOC_WORKER | Self VIEW; Manager MODIFY; HR Partner MODIFY |

Managers cannot see compensation in v1. This is a deliberate [Lab] choice that keeps "manager sees the worker but not the salary" testable.

### Business-process initiation policy

- **Change Job:** Manager and HR Partner.
- **Time Off:** Self.

### Compensation (USD, effective 2025-01-01)

| Worker | Salary |
|---|---|
| Dana | 300k |
| Alice | 200k |
| Priya | 190k |
| Frank | 160k |
| Bob | 120k |
| Grace | 115k |
| Carol | 110k |
| Henry | 105k |
| Connie | 108k |

All job revisions are effective 2025-01-01.

### Accounts

- Each human has username = lowercase first name, password `pw-<username>` (synthetic).
- ISUs:
  - `isu-directory`, in Directory Reader.
  - `isu-eng-reader`, in Engineering Reader.

### API clients

| Client | Scope ceiling | Bound to |
|---|---|---|
| `assistant` | staffing, absence, documents | delegation client |
| `hr-assistant` | staffing, compensation, absence, documents | delegation client |
| `directory-sync` | staffing | `isu-directory` |
| `eng-sync` | staffing | `isu-eng-reader` |

Client secrets are synthetic: `secret-<client_id>`. Disposable Compose database passwords are `mw-owner-lab` for `mw_owner`, `mw-app-lab` for `mw_app`, and `postgres-lab` for bootstrap. They are fixed local lab credentials, not external credentials.

### Documents

| Title | Domain / target | Classification |
|---|---|---|
| Employee Handbook | DOC_TENANT | INTERNAL |
| Compensation Policy | DOC_TENANT | INTERNAL; contains prompt-injection text |
| Engineering Reorg Plan | DOC_ORG on Engineering | CONFIDENTIAL |
| Bob Performance Review | DOC_WORKER, owner Bob | CONFIDENTIAL |

### Globex

- Dave (Manager of `SO-GX`) and Eve, with compensation.
- A Globex handbook.
- Accounts `dave` and `eve`.

### M2 seed addition

A pending Time Off request for Grace whose `reason` contains prompt-injection text.

---

### M3 bulk-v1 additions [Lab policy]

Ordinary startup, reset and tests keep Acme/Globex, their IDs, credentials and
visibility unchanged. Explicit `make seed-bulk` adds Northstar (512 workers,
43 orgs, 600 positions), Meridian (1,024/85/1,200) and Cedar (2,048/171/2,400).
The generator uses seed 20261008, SHA-256-derived per-tenant RNG streams, fixed
iteration/templates and UUID5 IDs. Each tenant manifest contains table counts,
canonical table hashes, per-document length/hash and injection/control labels;
no password hashes or private keys are part of the manifest. Shared synthetic
password `pw-bulk-{slug}` uses one randomly salted KDF hash per tenant, reused
across bulk accounts `worker-00001`, etc.; original small passwords are unchanged.

Each worker has three job/compensation revisions (2024/2025/2026 January 1),
two time-off events, one completed Change Job and four 4–16 KiB documents.
Supervisory trees have 4–5 levels, parent-org managers, constrained HR/comp
roles, unfilled leadership positions and vacant positions for live changes.
Promotions use dedicated lower-level historical positions in the same family;
other workers keep their position across merit reviews. Occupancy never overlaps.
Completed job events link to 2026 revisions; seed approvals are imported
fixtures, not live business transactions. Two balance snapshots (2026-01-01 and
seed day) agree with completed seeded absence days at eight hours/day. Live
absence approval still does not alter these snapshots.

The reviewer-approved bulk-v1 revision (before any deployed bulk load) uses
200 synthetic first and 200 last names, with deterministic middle initials for
collisions and no numeric suffixes. Ten job families occupy their own subtrees:
Engineering, Product, Sales, Marketing, Customer Support, Finance, People, Legal,
IT and Operations. Northstar is software, Meridian healthcare and Cedar retail;
each has industry-specific departments, projects and named office locations.
A roughly 8% management layer follows org depth (VP, Director, Senior Manager,
Manager). IC level weights are 25/38/25/9/3 for Associate/II/Senior/Staff/Principal.
Synthetic family/level salary bands give Engineering and Legal higher base pay
than Support/Operations; historical merit increases and promotions match position
history. Offers show the same three-year pay figures as compensation revisions.
Document sections vary by type, project, office, review findings and action;
exact byte targets, IDs/counts and the manifest hash mechanism remain unchanged.
Content hashes necessarily change with the richer content. No deployed seed
migration or version bump is needed because bulk-v1 has not been deployed.

Documents include offers, performance/onboarding notes, acknowledgements,
handbooks, policies and org plans. At least 1% are inert injection fixtures,
with benign controls; URLs use `exfil.invalid`. Domain/classification and current
object access apply equally to fixtures and normal documents. Manifest labels
are not exposed as model safety guarantees. ASU seed additions are deferred to
slice 2b; events/AI are never invoked during import.

The owner importer runs locally in the existing Compose service, or as a
one-off bootstrap-image command against RDS/S3 at approved deployment. It uses
100-row transactions and sequential object writes (within the eight-write cap).
A session advisory lock serializes imports per tenant; a version/checksum marker
and disabled tenant gate protect partial loads. Matching rows and objects are
verified/skipped; a changed row or manifest fails, never overwritten. An
incomplete load resumes; missing rows in a completed load require explicit
reset. Seed objects use immutable conditional puts, verifying matching bodies
on retry. Failed imports can leave unreferenced deterministic keys, never visible
through the API. A full admin reset is explicit and destructive; it does not
silently reset data on application startup. S3 cleanup is a separate owner task.

`make test-bulk` uses a throwaway PostgreSQL cluster without AWS or Docker.
The measured size and commands are recorded in the README. Schema changes use
fresh disposable databases; there is no migration framework or implicit ALTER
of an already installed D1 database.

---

## 11. Acceptance tests

Test IDs are stable references for the runtime project. A **(D)** marks a delegated-principal variant.

### M1: Identity and tenancy

| ID | Scenario | Expected |
|---|---|---|
| T-ID-01 | Acme token used on the Globex host | 401 |
| T-ID-02 | Token with `alg=none` or HS256 | 401 |
| T-ID-03 | Expired token (advance clock 301 s) | 401 |
| T-ID-04 | Disabled account's unexpired token | 401 |
| T-ID-05 | Delegated token after grant revocation (same token, unexpired) | 401 |
| T-ID-06 | Delegated token after client disabled | 401 |
| T-ID-07 | Token exchange with a grant belonging to another client | 401 `INVALID_GRANT` |
| T-ID-08 | Grant scopes beyond the client ceiling | 422 |
| T-ID-09 | Key rotation: old-key token still valid until the key is retired; then 401 | as stated |
| T-ID-10 | `tenant_id` in body or query of any request | ignored |
| T-DB-01 | `mw_app` reads without tenant context; with Globex context, reads Acme rows by ID without a tenant predicate and attempts an Acme insert into an insertable table | no data without context; cross-tenant read empty; insert rejected by RLS `WITH CHECK` |
| T-DB-02 | `mw_app` attempts `UPDATE` or `DELETE` on audit tables | permission denied |
| T-DB-03 | `mw_app` attempts `UPDATE` or `DELETE` on job/compensation revisions and workers; inspect its table/column privileges | permission denied; grants match §1 least-privilege matrix |

### M1: Visibility matrix (reads as of the seed date)

| ID | Caller → target | Basic | Compensation |
|---|---|---|---|
| T-V-01 | Bob → Bob | 200 | 200 |
| T-V-02 | Bob → Alice | 404 | — |
| T-V-03 | Alice → Bob | 200 | 403 |
| T-V-04 | Alice → Grace (Manager, all subordinates) | 200 | 403 |
| T-V-05 | Frank → Bob (sibling, no reach) | 404 | — |
| T-V-06 | Carol → Bob | 200 | 200 |
| T-V-07 | Carol → Grace (Henry covers Platform) | 404 | — |
| T-V-08 | Henry → Grace | 200 | 200 |
| T-V-09 | Connie → Bob | 200 | 200 |
| T-V-10 | Alice → Globex Eve (on the Acme host) | 404 | — |
| T-V-11 | Dana → Grace (Executive, all subordinates) | 200 | 403 |
| T-V-12 | Alice (D, `assistant`) → Bob | 200 | 403 |
| T-V-13 | Carol (D, `assistant`: no compensation scope) → Bob compensation | — | 403 |
| T-V-14 | Carol (D, `hr-assistant`, grant with `compensation`) → Bob compensation | — | 200 |
| T-V-15 | Carol (D, `hr-assistant`, grant **without** `compensation`) → Bob compensation | — | 403 |
| T-V-16 | Bob history as Alice | job rows only, no salary fields | |
| T-V-17 | Revoke Carol's HR Partner role on Engineering; her next request for Bob | 404 | |
| T-V-18 | `isu-eng-reader` → Bob / Grace / Priya | 200 / 200 / 404 | |
| T-V-19 | `isu-directory` → Bob compensation | 403 | |

### M1: Documents

| ID | Scenario | Expected |
|---|---|---|
| T-D-01 | Bob reads the handbook / reorg plan / his review | 200 / 404 / 200 |
| T-D-02 | Alice reads the reorg plan / Bob's review | 200 / 200 |
| T-D-03 | Frank reads Bob's review | 404 |
| T-D-04 | Carol creates a `DOC_ORG` document on Engineering with classification INTERNAL | 422 (below minimum) |
| T-D-05 | Bob creates a `DOC_WORKER` document for himself | 403 (Self has VIEW only) |
| T-D-06 | Exfiltration path: Carol reads Bob's compensation, then creates a `DOC_ORG` document containing it; Alice reads it | compensation GET 200, document POST 201, document GET 200 (documented composition gap) |
| T-D-07 | Confidential document read while audit writes fail. The test simulates the failure at the database: as `mw_owner`, `REVOKE INSERT ON audit_authz FROM mw_app`, run the read, then restore the grant. The HTTP fault API stays in M2. | 503, no content returned |

### M1: Pagination and rate limiting

| ID | Scenario | Expected |
|---|---|---|
| T-P-01 | `isu-directory` scans Acme workers with `limit=3` | all visible workers exactly once; `next_cursor` null at the end |
| T-P-02 | Cursor replayed by another account, with different filters, or tampered | 400 `INVALID_CURSOR` |
| T-P-03 | Alice scans Engineering with `include_subordinates`, `limit=1`; after the first page her Manager assignment is revoked (admin); next page | 200 with no rows and `next_cursor` null; no error |
| T-P-04 | Rate limit capacity 2 for `directory-sync`; 3rd request | 429 with `Retry-After`; succeeds after advancing the clock |
| T-P-05 | Continue a scan after a 429 using the same cursor | no duplicates, no gaps |
| T-P-06 | Alice lists Engineering workers with `include_subordinates` | Bob, Frank, Grace; not Priya |

### M1: Effective dating

| ID | Scenario | Expected |
|---|---|---|
| T-E-01 | Bob's compensation `as_of=2024-06-01` | 404 (before his first revision) |
| T-E-02 | Revisions inserted directly by the test fixture for future dates are invisible until the clock reaches them | as stated |

### M1: Audit

| ID | Scenario | Expected |
|---|---|---|
| T-A-01 | Carol reads Bob's compensation | `audit_authz` row with matched group HR Partner, constraining org Engineering, `policy_version` |
| T-A-02 | Bob is denied Alice | denial row |
| T-A-03 | Grant creation and revocation | `audit_objects` rows |

### M1: Additional coverage of the existing contract

| ID | Scenario | Expected |
|---|---|---|
| T-ID-11 | Current client/grant scopes shrink after issuance; delegated and ISU tokens reused | current intersection enforced |
| T-ID-12 | Grant management with ISU/delegated token; ownership, TTL bounds and expiry | direct human only; own grants only; TTL enforced |
| T-ID-13 | Bad credentials, ISU password login, or client credentials on an unbound client | 401 |
| T-ID-14 | Unknown host, missing bearer, caller/generated request ID | uniform error body and echoed request ID |
| T-V-20 | HR Partner role assigned to a vacant position in Platform | does not prune Carol's reach |
| T-V-21 | Manager group configured CURRENT_ONLY | sees Bob, not Grace |
| T-V-22 | Worker organizations, organization detail/workers, direct reports | correct references, membership and authorization |
| T-V-23 | Follow a worker position link using human, delegated or ISU tokens; unknown/cross-tenant position; missing token | documented position shape; any authenticated tenant principal allowed; 404 for missing/cross-tenant ID; 401 without authentication |
| T-D-08 | Authorized document list | metadata only, no content; unauthorized documents omitted |
| T-D-09 | Document target mismatch, UTF-8 content >64 KB, tenant-document write | 422 for invalid input; 403 for seed-only tenant writes |
| T-D-10 | Document creation while object-audit INSERT fails | 503; document and authorization audit roll back |
| T-P-07 | Document keyset pagination and filters; limit >200 | complete metadata scan; filters honored; invalid limit rejected |
| T-E-03 | Two compensation revisions with the same effective date | latest recorded sequence wins |
| T-A-04 | Delegated sensitive read | audit records human, client and grant |
| T-A-05 | Bob lists workers/documents with unauthorized rows omitted, then directly requests Alice | list filtering writes no audit rows; direct denial is audited |
| T-AD-01 | Admin disabled or accessed through public app | absent; never on public app |
| T-AD-02 | Reset after mutations | deterministic seed restored; keys regenerated; clock and limits reset |
| T-AD-03 | Requests while the admin clock is enabled | no wall-clock drift; only set/advance moves time |

### M2: Business processes

| ID | Scenario | Expected |
|---|---|---|
| T-B-01 | Alice initiates Change Job: Bob → `P-FIN-1`, effective 2026-11-01, salary 140k | 201; steps RECEIVING_MANAGER (Priya), COMPENSATION_PARTNER (Connie) |
| T-B-02 | Priya views the event before Nov 1 | 200 without compensation fields; Priya → Bob basic 404 |
| T-B-03 | Priya approves; Connie approves | `SUCCESSFULLY_COMPLETED`; revisions recorded |
| T-B-04 | Before Nov 1: Alice → Bob 200, Priya → Bob 404. On or after Nov 1 (advance clock): Alice → Bob 404, Priya → Bob 200 | as stated |
| T-B-05 | After Nov 1: Alice reads Bob `as_of=2026-10-15` | 404 (current authority) |
| T-B-06 | Carol initiates while Alice's event is pending | 409 `PENDING_CHANGE_EXISTS` |
| T-B-07 | Two concurrent initiations for Bob (threads) | exactly one 201, one 409 |
| T-B-08 | Approve versus deny race with the same `expected_version` | one succeeds; loser gets 409 `INVALID_STATE` if the winner made the event terminal, otherwise `VERSION_CONFLICT` |
| T-B-09 | Final approval after the effective date (advance clock to Nov 2) | 409 `EFFECTIVE_DATE_PASSED`; no revisions; cancel succeeds |
| T-B-10 | Alice tries to approve her own initiated event | 403 |
| T-B-11 | Bob tries to approve or deny his own event | 403 |
| T-B-12 | Change Job without compensation | compensation step SKIPPED; completes after Priya |
| T-B-13 | Target position occupied as of the effective date | 409 `POSITION_OCCUPIED` |
| T-B-14 | Alice (D, `assistant` with `staffing` only) initiates with compensation | 403 (scope) |
| T-B-15 | Two different workers concurrently target the same vacant position | exactly one 201; loser 409 `POSITION_OCCUPIED` |
| T-B-16 | Connie loses subject compensation reach; receiving step then compensation step completes | compensation hidden while PENDING, visible while currently eligible and AWAITING, hidden after completion; event remains visible |
| T-B-17 | Event list filters, awaiting_me, pagination, and hidden rows | current visibility and field filtering; no denial audit for omitted rows |
| T-B-18 | Invalid Change Job date, salary, or currency | 422 |
| T-B-19 | Destination becomes occupied before final approval | 409 POSITION_OCCUPIED; event unchanged |
| T-B-20 | Globex principal reads or acts on Acme event | 404; event unchanged |
| T-B-21 | Direct manager is initiator; ancestor manager present or absent | route upward; missing manager stalls, cancel remains available |
| T-T-01 | Bob requests time off; Alice approves | completed |
| T-T-02 | Bob's request pending across his Nov 1 transfer: Alice approves after Nov 1 | 403; Priya succeeds |
| T-T-03 | Cancel versus approve race | one wins, one 409 |
| T-T-04 | Frank reads Bob's time-off reason | 404 |
| T-T-05 | Grace's seeded request: Frank (direct manager) sees the reason, which contains injection text | 200 (the text is data) |
| T-T-06 | Invalid time-off range, another worker as initiator, valid past dates | 422, 403, 201 respectively |

### M2: Idempotency and faults

| ID | Scenario | Expected |
|---|---|---|
| T-I-01 | Same key, same body, sent twice | second response is a replay with the same receipt; one event exists |
| T-I-02 | Same key, different body | 422 |
| T-I-03 | `timeout_after_commit` on Priya's approve, then retry | 504, then a replayed receipt; the step is approved once |
| T-I-04 | `timeout_before_commit` on approve, then retry | 504, then normal success |
| T-I-05 | Connie approves (`timeout_after_commit`); her Compensation Partner role is revoked; retry | receipt returned; no compensation values in the response |
| T-I-06 | Delegated approve with `timeout_after_commit`; grant revoked; retry | 401 |
| T-I-07 | Replay after 24 hours (advance clock) | treated as new; business rules apply (e.g. 409 `INVALID_STATE`) |
| T-I-08 | Two concurrent requests with the same key | one executes; the other receives a replay or blocks then replays |
| T-I-09 | Missing/overlong key; same JSON with reordered keys and whitespace | 400; canonical-body replay |
| T-I-10 | Client disabled after successful delegated action; replay | 401 |
| T-I-11 | Successful delegated action, then grant scopes removed while grant remains active | minimal receipt replay, no event fields and no re-execution |
| T-F-01 | `latency`, `status` 503, and 429 faults apply only to matching requests and only `count` times | as stated |
| T-F-02 | Injected audit failure on write, sensitive read, and denied read | 503 with rollback, 503, original denial; subsequent request succeeds |
| T-F-03 | Final revision audit fails in PostgreSQL | revisions, history, transition, and idempotency receipt roll back together; retry succeeds |

---

### M3 slice 2a: storage, bulk seed and balances

| ID | Acceptance |
|---|---|
| T-M3-S-01 | Local/API shapes unchanged; server-derived S3 paths; unauthorized/cross-tenant access before cloud calls; lists metadata only |
| T-M3-S-02 | SSE-KMS/Bucket Key immutable upload; integrity/missing/upload/audit failure; rollback cleanup; post-commit timeout keeps object; seed retry verification |
| T-M3-SEED-01 | Full deterministic counts/manifests/injection controls; old fixtures unchanged; position occupancy, field visibility and pagination |
| T-M3-SEED-02 | Full load/rerun, modified-row rejection, partial tenant unavailable, resume, no events/inference, RDS/local size |
| T-M3-BAL-01 | Snapshot dates and quarter-hour schema; current ABSENCE scope/reach; cross-tenant RLS; owner-only writes; no live debit |
| T-M3-ABAC-02 | Tagged STS calls, per-tenant cache separation, concurrent refresh, disabled/unknown tenant rejection; no cached fallback on refresh failure |

T-M3-S-03 and T-M3-ABAC-01 are reserved for checkpoint-4 live policy proofs;
report session-expiry caps belong to slice 2c. No local stub test claims AWS
policy enforcement.

### M3 slice 2b: identity, credentials, attribution and logs

| ID | Acceptance |
|---|---|
| T-M3-ID-01 | Tenant registration uniqueness; maximum one ASU per mode/two total; one client each; disabled default; RLS and privileges |
| T-M3-ID-02 | Ambient JWT success, exact issuer/subject/audience; certificate pin/validity; wrong tenant/client/mode/algorithm/key fails |
| T-M3-ID-03 | Assertion expiry/future time/60-second limit, concurrent JTI replay, token TTL caps |
| T-M3-ID-04 | OBO renewal without human token; client/credential/grant required; wrong tenant/client, expired/revoked grant and disabled human rejected; current human authority rechecked |
| T-M3-ID-05 | Current scope and operation intersection; human action allowed but delegate denied; ambient domain reach and BP denial |
| T-M3-ID-06 | Legacy token/grant compatibility; no ASU fallback to legacy exchange or ISU credentials |
| T-M3-CR-01 | Two-version overlap boundary, rotation failure, duplicate-key rejection, emergency revocation and old-token rejection |
| T-M3-CR-02 | Disable registration/ASU/client, secret outage, inactive secret version; no secret material in registry/responses/logs |
| T-M3-A-01 | By User/OBO attribution across modes, sensitive reads/BP actions/idempotent replay, legacy attribution, issuance/config audit failure |
| T-M3-A-02 | Structured JSON request IDs and redaction, 3-day CloudWatch retention/encryption in plan and live checks |

CloudWatch retention/encryption and live IAM enforcement remain checkpoint-3/4 checks.

### M3 slice 2c: exports and business notifications

| ID | Acceptance |
|---|---|
| T-M3-R-01 | Report current row/field authorization, scope/operation checks, snapshot, size limit, no document-body export |
| T-M3-R-02 | Export audit failure/S3 failure gives no URL; TTL caps and wall time; URL reuse, expiry and grant-revocation limitation; lifecycle cleanup |
| T-M3-E-01 | Commit-only types/schema/tenant/actor/version; final vs intermediate approval; no sensitive content or authority |
| T-M3-E-02 | Per-entry PutEvents failures, retry/timeout duplicate ID, atomic outbox/history rollback, restart recovery, crash after acceptance before mark, backoff and bounded repair; rollback emits nothing |
| T-M3-E-03 | Cross-domain tenant filtering, no credentials in events; duplicates/out-of-order fixtures and authorized HTTP refetch |
| T-M3-E-04 | One DLQ per target, policy/KMS correctness; permanent target failure retained, alarms visible, controlled redrive preserves detail ID |

Local/stub tests prove protocol and transaction behavior. S3 lifecycle, actual
cross-domain filtering, DLQ policies/KMS/alarms remain checkpoint-3/4 proofs.

## 12. Reproducible, disposable environments [Lab requirement]

All data is synthetic, so every environment must be disposable: destroying it loses nothing, and recreating it yields the same Acme and Globex world.

- **Deterministic state:** schema plus deterministic seed (§2, §10) fully reconstructs application state. Signing keys and runtime-created records are regenerated, not preserved.
- **Local:** Docker Compose via `make up` / `make down`. Tests never require Docker.
- **AWS (milestone D1, after M1; not part of M1):** a separate spec, written when D1 starts, must satisfy:
  - Everything is provisioned by Terraform under `infra/`, with no manual console steps and no dependence on console-created resources.
  - `terraform destroy` is a normal, documented workflow, not disaster recovery.
  - Terraform state lives outside the destroyed environment: local state (gitignored) initially, a remote backend later if useful.
  - Every resource carries `Project=mock-workday` and `Environment=<name>` tags.
  - Wrappers: create, deploy, migrate and seed, run acceptance tests against the deployment, destroy.
  - A leftover check after destroy lists tagged resources and common cost leaks: load balancers, NAT gateways, EBS volumes, Elastic IPs, RDS instances and snapshots, CloudWatch log groups.
  - Prefer resources that cost nothing when idle. Anything always-on and billable is called out in the README with its approximate cost.
  - Respect the account's constraints: the selected Region (`us-east-2`), the account plan's supported services, and any spend limit.

## 13. Deliberate limitations (v1)

- Rate-limit and fault state live in process memory; there is one service process.
- Organizations, positions, and role assignments are not effective-dated.
- Bearer tokens are not bound to the caller.
- No rescind, send-back, correction, or Workday protocols beyond this REST-style API.
- Allowed non-sensitive reads are not audited.
