# Mock Workday: proposed plan (Claude)

**Status:** proposal for review. Nothing here is implemented. Statements about Workday are labeled; anything not yet checked against primary Workday documentation is marked *unverified*.

**North star:** Mock Workday is a protected multi-tenant system containing:

- structured HR data
- sensitive documents
- contextual authorization
- one low-risk self-service workflow and one consequential, effective-dated workflow
- strong audit semantics

Once that works, its API is versioned and kept stable. Everything else interesting belongs in the Agent Cell Runtime.

**Boundary rule:** Mock Workday exposes operation shapes; the Agent Cell Runtime owns execution patterns. For example, Mock Workday paginates and throttles, while the runtime decides to run a bulk agent, back off, and checkpoint. Likewise, Mock Workday offers self-service actions, while the runtime decides whether an agent may perform them automatically.

## Purpose

Mock Workday is a small, multi-tenant HCM system modeled on public Workday concepts ("GMS-lite"). It is the enterprise system the future Agent Cell Runtime must protect and operate against.

It must be:

- **Independent.** No agent, cell, or execution concepts. Usable by ordinary HTTP clients. The runtime is just another API consumer.
- **The final authority.** It authenticates and authorizes every request itself. A caller saying "I already checked this" grants nothing.
- **Workday-shaped where it matters.** Fidelity is spent where it changes the runtime's problem (identity, security, business processes, effective dating, references); everything else is simplified.

## Guiding rule

> Copy Workday in depth, not breadth. Include something when it creates a meaningful runtime lesson, not merely because Workday has it.

Labels used in the spec:

- **Workday fact**: supported by public Workday documentation (cite source).
- **Workday-inspired**: modeled on a Workday concept, simplified.
- **Lab policy**: our own choice; makes no claim about Workday.

## Boundaries

```text
customer agents (untrusted)  ->  Agent Cell Runtime  ->  Mock Workday API  ->  Mock Workday DB
                                 (later project)         (authenticates and
                                                          authorizes independently)
```

- **Separate repository (decided).** This repository owns Mock Workday's code, database, and process. The runtime lives in [`agent-cell-runtime`](https://github.com/beejmaxx/agent-cell-runtime). Separate repositories make the ownership boundary mechanical, including for coding agents that might otherwise import or copy internals.
- **Published contract.** Mock Workday publishes an OpenAPI document and a container image. The runtime consumes only those: no shared ORM models, no database access. Integration tests run the published image alongside the runtime.
- Mock Workday is built first. After its exit criteria pass, it changes only through versioned API changes driven by runtime needs.

## v1 scope

1. Tenancy and identity
2. A minimal HCM graph
3. Security
4. Documents
5. Two business processes: Request Time Off (low risk) and narrowed Change Job (high consequence)
6. Audit

### Operation shapes covered

These are the kinds of interaction the runtime must handle, independent of which Workday application a customer uses. Payroll, recruiting, expenses, and accounting mostly produce more instances of the same shapes.

| # | Shape | Example | Provided by |
|---|---|---|---|
| 1 | Ordinary personal read | "Who is my manager?" | Mock Workday |
| 2 | Sensitive read | "What is my compensation?" | Mock Workday |
| 3 | Protected unstructured read | "What does the compensation policy say?" | Mock Workday |
| 4 | Low-risk delegated write | "Request Friday off." | Mock Workday |
| 5 | Consequential multi-party write | "Move Bob to Finance and change his salary." | Mock Workday |
| 6 | Bulk paginated read | "Scan all workers I am authorized to see." | Mock Workday |
| 7 | Autonomous principal | An integration identity, not a human | Mock Workday |
| 8 | External irreversible side effect | Send an email | **Agent Cell Runtime** (fake tools), not Mock Workday |

## 1. Tenancy and identity

### Tenants

- Acme and Globex, addressed by endpoint (`acme.mockworkday.local`), never by a request field.
- Storage: one PostgreSQL database with row-level security (lab policy).
- The tenant is established from the endpoint and token; RLS is a backstop, not the source of truth.
- The application connects as a non-owner role with `FORCE ROW LEVEL SECURITY`.
- Tenant context is set per transaction (`SET LOCAL`) so pooled connections cannot leak it.

### Principals

- Human users.
- Integration system users (ISUs) for autonomous access (Workday-inspired).
- Registered API clients with scope ceilings.

### Tokens

- Mock Workday is the issuer.
- Short-lived signed tokens carry identity only (`iss`, `aud`, `sub`, `act`, `scope`, `exp`, `jti`).
- Group membership is resolved per request, so revocation takes effect immediately.
- No full OAuth flows in v1.
- **Lifetimes (decided, lab policy):**
  - Access tokens last 5 minutes.
  - Delegation grants default to 1 hour, with a 24-hour maximum, and can be revoked.
  - Lifetimes are deliberately short so tests hit expiry during long-running work.
- Token expiry uses the same controllable clock as effective dating.

### Delegation (lab policy; required by the runtime)

- A user explicitly creates a scoped, expiring grant for an API client.
- The client exchanges that grant for a delegated token.
- Minimum surface: one endpoint to create grants and one token endpoint.
- Delegated identity is always issued by Mock Workday, never asserted by the caller.

```text
effective access = user's current permissions
                 ∩ client scope ceiling
                 ∩ delegation grant scopes
```

A compromised caller is limited to the grants it currently holds; it cannot act as arbitrary users. This is our lab design, not a claim about how Workday implements delegation.

**Known limitation:** bearer tokens are not bound to the caller in v1, so a stolen token works until expiry.

## 2. Minimal HCM graph (Workday-inspired)

- **Supervisory organizations:** a hierarchy with manager role assignments. Per Workday (*unverified*), a manager is not a member of the org containing their direct reports.
- **Position Management:** positions exist independently of workers, can be open or filled, and carry organization assignments and roles even when vacant.
- **Workers:** fill positions and carry business reference IDs alongside opaque object IDs.
- **Effective-dated job and compensation:**
  - Stored as rows with effective dates.
  - "Current" means the latest row effective as of a given date.
  - Future-dated changes work without a scheduler.
  - Tests use a controllable clock.
- **API shape:** related objects are returned as references (`{id, descriptor, href}`) in the style of Workday's public APIs (*unverified*).
- **Deferred:** company and cost center, job profiles, and worker types.

## 3. Security (Workday-inspired)

```text
principal -> role assignments / self / integration group
          -> contextual security group membership
          -> domain policy (view/modify, get/put)
          +  business-process policy (initiate, approve, deny, cancel)
```

### Model

- **Security groups:** EmployeeAsSelf, Manager, HR Partner, Compensation Partner, plus integration security groups.
- **Role constraints:** roles are assigned to positions on organizations. Access applies to that organization and, where configured, its subordinates.
- **Domains:**
  - worker basic data
  - worker organizations
  - worker compensation
  - documents (one or more document domains)
- Compensation is secured separately from basic worker data.
- **One authorization pipeline** for humans and integrations; only authentication differs.

### Read rules

- Every read path is authorized: worker history, business-process events, direct reports, and documents.
- History is field-filtered by domain.
- **Visibility (lab policy):** an object the caller cannot see returns 404; lists omit unauthorized rows.

### Configuration

- Security and process configuration comes from seed data only. There is no admin API or UI.
- Every security change increments a recorded `policy_version`.

## 4. Documents

Documents are a protected resource, not a retrieval system: no RAG, embeddings, ingestion, or SharePoint.

**Fields:**

```text
Document
  id
  title
  content          (text, stored in Postgres)
  owner_worker_id? (worker-scoped documents)
  security_domain  (who may read or write; Mock Workday enforces this)
  classification   (sensitivity metadata, returned to callers)
```

**Endpoints:** `GET /documents`, `GET /documents/{id}`, `POST /documents`, all authorized like any other resource.

**Classification:**

- Mock Workday enforces only the security domain.
- Classification is returned as metadata for downstream consumers. For example, the runtime can later use it to restrict which models may see content.
- Mock Workday has no agent concepts, so it does not act on classification itself.

**Writes:** `POST /documents` is deliberately included. A writable document that others can read is an exfiltration channel the runtime must defend against.

**Content:**

- Text only; no PDF parsing.
- Blob storage (S3) belongs to the runtime's AWS stage.

**Seed examples:**

- Acme tenant-wide: employee handbook, compensation policy, Engineering reorganization plan.
- Acme worker-scoped: Bob's performance review.
- At least one document contains prompt-injection text.
- Globex: entirely different documents.

## 5. Business processes

Two processes share one small process model: a low-risk self-service request and a consequential multi-party change. The contrast lets the runtime later apply different risk and confirmation policies, e.g. "Bob asked his agent to request Friday off" versus "Bob's agent wants to change someone else's compensation."

### Request Time Off (low risk)

```text
TimeOffRequest
  worker_id, start_date, end_date, reason (free text), status
```

- The employee initiates for themselves; their manager approves or denies.
- The employee may cancel while the request is in progress.
- No balances, accruals, calendars, carryover, or regional policy.

### Change Job (high consequence): example

- Move Bob from Engineering to Finance, optionally changing salary from 120k to 140k, effective Nov 1.
- One operation changes:
  - worker state
  - effective-dated data
  - organizational relationships
  - who can access the worker
  - possibly compensation

### Engine

- Process steps and assigned security groups are seeded per tenant.
- There is no configuration API or UI. The data model can describe multiple process types; v1 has exactly two.
- **Guardrail:** build only what these two processes require. If the work starts turning into a general-purpose workflow engine, stop.

### Event model

- Process events are objects with ID, type, subject, initiator, `initiated_at`, `effective_date`, `completed_at`, step history, version, and free-text comments.
- Comments provide another prompt-injection surface.

### Statuses

```text
IN_PROGRESS(step n) -> SUCCESSFULLY_COMPLETED | DENIED | CANCELED
```

The effect is applied in the same transaction as the final step, so "approved but not applied" never exists.

### Change Job routing (decided, lab policy)

1. **Initiate:** Bob's current manager (Alice) or his HR Partner (Carol). Two authority paths for the same action.
2. **Receiving manager approval:** Priya, the Finance manager, must approve moves into her organization.
3. **Compensation Partner approval:** required whenever the change modifies compensation, and only then. There is no dollar threshold.

### Process participation is not worker access

Before the effective date, Priya has no domain access to Bob, yet she must approve his move.

- An assigned step grants visibility of that process event and the fields the step needs.
- It does not grant general read access to the worker.
- An agent acting for Priya must respect the difference between "may act on this task" and "may read this worker".

### Rules (lab policy)

- The actor's authority is checked when they act, not when the process began.
- Initiators cannot approve their own step.
- Subjects cannot approve or deny their own events.
- Initiators may cancel an event while it is in progress.

### Access switchover

Before Nov 1, Alice still sees Bob and the Finance manager does not. On and after Nov 1, the reverse holds. As-of reads demonstrate both.

## Runtime-facing API contract

- **Idempotency keys** on initiation and step actions:
  - Scoped to tenant plus principal.
  - Bound to a hash of the request.
  - Reusing a key with a different request is rejected.
- **Optimistic concurrency:** step actions name the expected step and event version; a lost race returns 409.
- **Error semantics** documented for 401, 403, 404, 409, 429, and 503.
- **Correlation:** caller-supplied request IDs are recorded in audit and grant no authority.
- **Pagination:** list endpoints (e.g. `GET /workers?org=...&limit=50&cursor=...`) use opaque cursors over a stable order.
  - Authorization is evaluated on every page, so access lost mid-scan takes effect on the next page.
  - Cursors are bound to the caller and tenant.
  - Cursor expiry and behavior when data changes between pages are specified, not accidental.
- **Rate limiting:** per API client per tenant.
  - Deterministic and testable: driven by the controllable clock.
  - Returns 429 with `Retry-After`.

## 6. Audit

Three distinct records:

1. **Business-process history:** each step, actor, action, and time.
2. **Object audit:** who changed what, old and new values, when, and under which request ID.
3. **Authorization decisions:** allows and denials for sensitive reads, including:
   - the matched group
   - the constraining organization
   - the `policy_version`

Rules:

- Audit is append-only; the application's database role cannot update or delete it.
- Process transitions and their audit commit in the same transaction.

**Lab policy (decided, explicitly an experiment):**

- Sensitive reads (compensation, restricted documents) fail closed with 503 if their audit write fails. This favors the guarantee "no unaudited sensitive read" over availability.
- Denials remain denials even if auditing them fails.
- A later exercise challenges this tradeoff: measure the availability cost and compare alternatives such as serving the read, recording the gap, and alerting.

## Failure injection

Test-only and disabled by default. Reachable only on a separate admin interface the runtime cannot reach. It can inject:

- latency
- 429 and 503 responses
- timeout before commit
- timeout after commit (lost response)

## Stack (proposed)

Python, FastAPI, PostgreSQL, SQLAlchemy, pytest, and Docker Compose.

## Milestones

| Milestone | Scope | Exit evidence |
|---|---|---|
| **M1: Foundation** | Tenancy and identity (including delegation), the HCM graph with effective dating, security, documents, authorized read APIs and history, paginated listing, rate limiting, object and authorization-decision audit | Visibility matrix passes: self, inheritance, siblings, cross-tenant, compensation separation, document domains, delegation intersection, as-of reads, and role revocation mid-session. Bulk-scan contract passes: an integration user pages through the Acme workers it may see, receives deterministic 429s with `Retry-After`, continues with its cursor without duplicates or gaps, and loses access mid-scan when its permissions are revoked |
| **M2: Actions** | Process model, Request Time Off, narrowed Change Job, process history, idempotency, concurrency control, failure injection | Approve-versus-deny race, lost response after commit, conflicting key reuse, employee cancellation, step-scoped visibility for the receiving manager, and the effective-date access switchover behave as specified |

After M2, the Agent Cell Runtime begins. Mock Workday changes only through versioned API changes the runtime needs.

## Seed scenario (synthetic)

```text
Acme:   Dana (CEO, Executive)
          ├─ Alice (Manager, Engineering)
          │    ├─ Bob
          │    └─ Frank (Manager, Platform) └─ Grace
          └─ Priya (Manager, Finance)
        Carol (HR Partner assigned on Engineering and Finance; sits in HR)
        Compensation Partner: to be assigned in the spec
Globex: Dave (Manager) └─ Eve
```

## Belongs to the Agent Cell Runtime, not Mock Workday

- Scheduling: "every night at 02:00, launch the payroll audit agent."
- Execution patterns: backoff on 429, checkpointing bulk scans, resuming after a crash.
- Risk tiers: which operations an agent may perform automatically and which need human confirmation.
- External side-effect tools: `send_email`, web requests, other SaaS APIs, and model providers. These enable composition tests such as "the agent may read Bob's salary and may send email; can it email Bob's salary outside the company?"

## Backlog (not before the runtime)

- **Change bank details (first addition after M2):** combines a sensitive write, financial consequence, social engineering, prompt injection, human confirmation, fraud detection, and audit. Payroll diversion through a manipulated agent is the realistic version of "prompt injection causes harm." It requires a new payment domain, so it does not block the runtime.
- Termination
- Hire
- Rescind
- Query API and WQL-like reads
- Company and cost center security
- Caller-bound tokens (proof of possession)
- Full OAuth flows

## Non-goals

- Payroll, benefits, recruiting, and Financials
- SOAP, RaaS, and Graph protocols
- Admin UI and admin APIs
- Full bitemporal history
- Document retrieval, embeddings, and binary formats
- Time-off balances, accruals, calendars, and regional absence policy
- Scheduled jobs (the runtime owns scheduling)
- Single sign-on federation

## Before the detailed spec

1. Verify the Workday claims the design depends on against primary documentation:
   - role-assignment inheritance
   - manager membership semantics
   - business-process security actions and statuses
   - ISU and API-client authentication with scopes
   - effective dating
   - process history and audit features
   - public object and reference shapes
2. Resolve the remaining decisions:
   - subordinate-inheritance defaults
   - token format and signing keys
   - idempotency-key retention
   - which Acme worker holds the Compensation Partner role
3. Write the detailed spec and test matrix. Implementation starts only after approval.
