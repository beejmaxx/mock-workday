# Mock Workday: detailed specification (v1)

**Status:** draft for review. Implements [the plan](mock-workday-plan-claude.md). Workday evidence is in [workday-verification.md](workday-verification.md).

**Labels:**

- **[WD]**: verified Workday behavior.
- **[WD-inspired]**: a simplified Workday concept.
- **[Lab]**: our own policy.

Unlabeled statements are implementation choices.

**Milestones:**

- **M1:** §0–4, §6 except §6.4, §7, §8, §9 except faults, §10 except the M2 seed addition, and the M1 tests in §11.
- **M2:** §5 (business processes), §6.4 (idempotency), fault injection in §9, the M2 seed addition, and the M2 tests in §11.

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
│   ├── auth/             # tokens, keys, principals, grants
│   ├── authz/            # security groups, reach, decisions
│   ├── api/              # routers: oauth, workers, orgs, documents, bp, grants
│   ├── bp/               # business-process engine (M2)
│   ├── audit.py
│   ├── idempotency.py    # (M2)
│   ├── ratelimit.py
│   └── faults.py         # (M2)
└── tests/
```

**Processes:**

- One process serves two ASGI apps.
- **Public API:** port 8080 by default.
- **Test-admin API:** port 8081, mounted only when `MW_TEST_ADMIN=1`. It must never share the public port.

---

## 1. Tenancy

### Tenant resolution [Lab]

- The tenant comes from the `Host` header: `{slug}.mockworkday.local`, for example `acme.mockworkday.local`.
- An unknown host returns 404 `TENANT_NOT_FOUND`.
- A `tenant_id` or tenant slug in a request body or query is never read.

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

- **Per-transaction tenant context:** every request transaction begins with `SELECT set_config('app.tenant_id', :tid, true)`. The third argument (`true`) is equivalent to `SET LOCAL`, so the setting cannot leak through pooled connections. Code that needs a transaction must obtain it through `db.tenant_tx(tenant_id)`.
- **Global tables** (`tenants`, `signing_keys`) have no RLS. `mw_app` has `SELECT` on them; only `mw_owner` writes them.
- **Audit tables:** `mw_app` has `INSERT, SELECT` only, with no `UPDATE` or `DELETE`.

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
tenants(id uuid pk, slug text unique, name text)
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

accounts(id, tenant_id, username, kind HUMAN|ISU, worker_id null,
         password_hash, disabled bool, ui_sessions_allowed bool)
    -- HUMAN requires worker_id; ISU requires worker_id null and ui_sessions_allowed=false

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

domain_grants(tenant_id, domain, group_id, permission VIEW|MODIFY|GET|PUT)

api_clients(id, tenant_id, client_id text, secret_hash, name,
            scope_ceiling text[], isu_account_id null, disabled bool)
delegation_grants(id, tenant_id, user_account_id, client_id, scopes text[],
                  created_at, expires_at, revoked_at null)

documents(id, tenant_id, title, content text, domain, classification,
          owner_worker_id null, org_id null, created_by_account_id,
          created_by_client_id null, created_at)
    -- at most one of owner_worker_id, org_id is set; neither = tenant-wide

-- M2
bp_events(id, tenant_id, type CHANGE_JOB|REQUEST_TIME_OFF, subject_worker_id,
          initiator_account_id, initiator_client_id null, status,
          current_step int null, effective_date date null, payload jsonb,
          comment text, version int, initiated_at, completed_at null)
bp_steps(id, tenant_id, event_id, step_order, step_key, status, acted_by null,
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
           actor_client_id, comment, at)
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
    scopes: frozenset[str] | None  # None = unrestricted (direct human)
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
- **Permission families:**
  - Human and delegated principals need `VIEW` (read) or `MODIFY` (write).
  - ISU principals need `GET` (read) or `PUT` (write) [WD].
  - `MODIFY` implies `VIEW`, and `PUT` implies `GET`.

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
    need = {("human", READ): VIEW, ("human", WRITE): MODIFY,
            ("delegated", READ): VIEW, ("delegated", WRITE): MODIFY,
            ("isu", READ): GET, ("isu", WRITE): PUT}[(p.kind, action)]
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
| Lists | Unauthorized rows omitted silently |

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
- **Lock order:** the event row, then the worker row (`SELECT ... FOR UPDATE`). Initiation locks only the worker row.

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

1. Lock the worker row.
2. **Authorize:**
   - The initiator is a member, via §4.3 against the subject, of a group allowed to initiate. Seed: Manager and HR Partner role groups.
   - Delegated callers need `staffing`, plus `compensation` if a compensation change is included.
3. **Validate:**
   - `effective_date >= today`.
   - The target position exists in this tenant and differs from the current position.
   - The target position is vacant as of `effective_date`, and no pending Change Job targets it.
   - Compensation, if present: salary greater than 0 and a valid currency.
4. **One pending Change Job per worker:** reject with 409 `PENDING_CHANGE_EXISTS` if another `CHANGE_JOB` for the subject is `IN_PROGRESS`, or `SUCCESSFULLY_COMPLETED` with `effective_date > today`.
5. Insert the event, steps, process history, and audit records.
6. Store the idempotency record (§6.4).

**Steps (seeded per tenant):**

| Order | Key | Assignee | Condition |
|---|---|---|---|
| 1 | `RECEIVING_MANAGER` | `DIRECT_MANAGER(target_position.org)` | always |
| 2 | `COMPENSATION_PARTNER` | `ROLE_WITH_REACH(COMPENSATION_PARTNER, target_position.org)` | compensation present; otherwise `SKIPPED` |

**Final approval (one transaction; lock the event, then the worker):**

1. Check the version and step.
2. Check that the actor is the assignee (§5.2) at action time.
3. **Effective-date check:** if `effective_date < today`, return 409 `EFFECTIVE_DATE_PASSED` and change nothing. This check also applies to every non-final approval. Deny and cancel remain allowed.
4. Re-check that the target position is vacant as of `effective_date`.
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
- any account assigned to or acting on any step, past or current;
- principals whose memberships against the subject (now) match a group with read on the event's domain:
  - `WORKER_ORGANIZATIONS` for Change Job;
  - `ABSENCE` for Time Off.

**Field filtering:**

- `payload.compensation` and step comments on the compensation step are returned only to principals with `WORKER_COMPENSATION` read on the subject now, or to the `COMPENSATION_PARTNER` step's assignee or actor. Priya's receiving-manager step therefore shows the move without the salary.
- Time Off `reason` is visible to the same set of principals as the event itself.

### 5.6 Event endpoints

- `GET /api/v1/business-process-events/{id}`
- `GET /api/v1/business-process-events?awaiting_me=true&status=&subject=&limit=&cursor=`
- `POST /api/v1/business-process-events/{id}/approve`
- `POST /api/v1/business-process-events/{id}/deny`
- `POST /api/v1/business-process-events/{id}/cancel`

The action endpoints take the body `{"expected_step", "expected_version", "comment"}` and require an `Idempotency-Key`.

---

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
| `GET /api/v1/workers/{wid}/history` | revisions; compensation fields only with WORKER_COMPENSATION | M1 |
| `GET /api/v1/workers/{wid}/direct-reports?as_of=` | WORKER_BASIC per row | M1 |
| `GET /api/v1/organizations/{wid}` and `…/{wid}/workers` | organizations: any authenticated tenant principal [Lab]; workers: per row | M1 |
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

**Direct reports [WD-inspired]:** workers whose organization (as of `as_of`) has a filled position holding `MANAGER` directly that belongs to `{wid}`, plus the managers of those organizations' immediate sub-organizations.

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
  3. Run `SELECT … FOR UPDATE` on `(tenant, account, client_key, key)`, ignoring expired records.
     - **Exists with a different hash:** 422 `IDEMPOTENCY_KEY_REUSED`.
     - **Exists:**
       - Return the original status code with header `Idempotent-Replay: true` and the stored **receipt** `{operation_id, status, resource: ref}`.
       - The receipt is returned without re-running business authorization; it is never executed again.
       - Any additional stored response fields are included only if the caller's current read authorization permits them (for example, a compensation value).
     - **Absent:**
       - Insert the record. A concurrent duplicate blocks on the primary key and then sees the completed record.
       - Perform the operation and store the receipt.
       - Commit atomically with the operation.
- **Only successful operations are recorded.** A failed attempt changed nothing, so a retry re-evaluates.
- **Retention:** 24 hours by clock. An expired key is treated as new.

### 6.5 Rate limiting

- A token bucket per `(tenant, client_id)`, or `(tenant, account)` for direct human tokens.
- In-process state driven by the controllable clock, so it is deterministic.
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
| 422 | `VALIDATION_ERROR`, `IDEMPOTENCY_KEY_REUSED` |
| 429 | `RATE_LIMITED` |
| 503 | `AUDIT_UNAVAILABLE` |
| 504 | `SIMULATED_LOST_RESPONSE` (fault injection only) |

---

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

## 8. Audit

| Record | Written when | Transaction |
|---|---|---|
| `audit_authz` | Every sensitive read (compensation; history with compensation fields; documents ≥ CONFIDENTIAL), every write decision, every denial | Allowed sensitive reads and writes: same transaction, written **before** returning data. If the write fails, roll back and return 503 `AUDIT_UNAVAILABLE` [Lab experiment]. Denials: separate best-effort transaction; a failure is logged and the response stays a denial. |
| `audit_objects` | Every inserted revision, document, event status change, grant creation or revocation | Same transaction as the change |
| `bp_history` | Every initiate, approve, deny, or cancel action | Same transaction |

- Non-sensitive allowed reads (basic worker data, organizations) are not audited in v1.
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
| WORKER_BASIC | Self VIEW; Manager VIEW; HR Partner MODIFY; Compensation Partner VIEW; Directory Reader GET; Engineering Reader GET |
| WORKER_ORGANIZATIONS | Self VIEW; Manager VIEW; HR Partner MODIFY; Directory Reader GET |
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
| T-DB-01 | `mw_app` without `app.tenant_id` set reads tenant tables | zero rows or error, never data |
| T-DB-02 | `mw_app` attempts `UPDATE` or `DELETE` on audit tables | permission denied |

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
| T-D-06 | Exfiltration path: Carol reads Bob's compensation, then creates a `DOC_ORG` document containing it; Alice reads it | all 200 (documented composition gap) |
| T-D-07 | Confidential document read with audit failure injected | 503, no content returned |

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
| T-B-08 | Approve versus deny race with the same `expected_version` | one succeeds, one 409 `VERSION_CONFLICT` |
| T-B-09 | Final approval after the effective date (advance clock to Nov 2) | 409 `EFFECTIVE_DATE_PASSED`; no revisions; cancel succeeds |
| T-B-10 | Alice tries to approve her own initiated event | 403 |
| T-B-11 | Bob tries to approve or deny his own event | 403 |
| T-B-12 | Change Job without compensation | compensation step SKIPPED; completes after Priya |
| T-B-13 | Target position occupied as of the effective date | 409 `POSITION_OCCUPIED` |
| T-B-14 | Alice (D, `assistant` with `staffing` only) initiates with compensation | 403 (scope) |
| T-T-01 | Bob requests time off; Alice approves | completed |
| T-T-02 | Bob's request pending across his Nov 1 transfer: Alice approves after Nov 1 | 403; Priya succeeds |
| T-T-03 | Cancel versus approve race | one wins, one 409 |
| T-T-04 | Frank reads Bob's time-off reason | 404 |
| T-T-05 | Grace's seeded request: Frank (direct manager) sees the reason, which contains injection text | 200 (the text is data) |

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
| T-F-01 | `latency`, `status` 503, and 429 faults apply only to matching requests and only `count` times | as stated |

---

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
