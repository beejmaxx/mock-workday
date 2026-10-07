# MW-M3: Workday core integration — approved specification

**Status: approved with reviewer decisions, 2026-10-08.** Checkpoint 1 is
complete. Implement the approved contract in the independently reviewed slices
below; fold each slice into [spec.md](spec.md) with its code and tests. No push.

## 0. Scope, labels, and checkpoints

**[Lab policy]** Preserve synchronous FastAPI, SQLAlchemy 2 Core, psycopg 3,
PyJWT/cryptography, pytest, and plain `schema.sql`. Add small functions for cloud
service calls; no ORM, async conversion, workflow engine, or plugin framework.
Only Change Job and Request Time Off are business processes. Agent registration,
ASUs, their credentials and audit attribution are enterprise identity records.
There are no executions, cells, sandboxes, or runtime lifecycle objects here.

Labels apply to each paragraph, table, or subsection they introduce:

- **[Workday fact]**: supported by a linked Workday primary source.
- **[Workday-inspired]**: a simplified analogue, not a Workday wire contract.
- **[Lab policy]**: our design, including all AWS architecture and infrastructure.
  AWS links substantiate AWS behavior, never Workday's internal infrastructure.

**[Lab policy]** Read-only context: `~/code/aws/docs/scenarios.md` and
`~/code/aws/docs/identity-flow.md`. Their private context is not reproduced here.
The runtime team owns its implementation. No shared database, source package,
credential store, or direct access to Mock Workday storage is an integration API.

**[Lab policy]** Checkpoints from [AGENTS.md](../AGENTS.md):

1. Spec reviewed; commit the reviewer amendments before starting 2a.
2. Implement code/tests in four slices, **each committed and stopped for review**:
   - **2a:** S3/KMS/ABAC storage and local branch, bulk seed generator, balances.
   - **2b:** registration, ASUs, JWT-bearer/OBO, credential store, audit and logs.
   - **2c:** report exports and business events with transactional outbox.
   - **2d:** native AI, fake model, usage ledger and EMF.
   Fold each approved slice into `docs/spec.md`, §11 and generated OpenAPI in
   its implementation commit. Later-slice records in the bulk seed are deferred
   until that slice; 2a does not create ASUs or publish business notifications.
3. Implement approved Terraform/scripts, run `fmt`, `validate`, `plan`; commit
   and stop with the plan. No apply or image push at these earlier checkpoints.
4. Only after plan approval: apply, smoke-test, record evidence, then
   `make aws-down` and `make aws-leftovers`. Nothing billable stays up.

**[Lab policy]** All provisioned Regional resources stay in dev `729608197929`,
`us-east-2`. Bedrock has an explicit processing-location exception (§7.1);
no infrastructure is provisioned in its inference destinations.
Preserve D1's bootstrap, foundation, state bucket, stack boundaries, and tags.
Network IDs come only from `/lab/dev/network/vpc_id`, `public_subnet_ids`, and
`private_subnet_ids` in SSM. Never read foundation state or modify its resources.
Reviewer reports the Free-plan SCP permits events, sqs, secretsmanager, kms,
route53, s3, and bedrock; this is supplied environment context, not a verified
deployment. Verify WAF permission before checkpoint 4; do not change the SCP.

**[Lab policy]** Explicitly excluded: multi-AZ RDS, autoscaling, Cognito,
SNS, internal work queues, Object Lock, Athena, Kinesis, Firehose, AWS Private CA,
CloudFront (authenticated dynamic API/caching risk), X-Ray (instrumentation
excluded), Macie and CloudHSM (unavailable on this plan), and new runtime
infrastructure. WAF is now in scope on the public ALB only, superseding its
earlier exclusion. The transactional publication outbox (§6.2) is not an internal work queue.
The sole queue exception is a provider-side SQS
dead-letter queue for each cross-domain EventBridge target (§6).

## 1. Evidence and limits (A, B, H)

**[Workday fact]** Workday documents up to two ASUs per agent, one per mode,
each with its own OAuth client. Delegate access intersects user permissions with
allowed agent skills; ambient access uses the agent's permissions. Audit names
the ASU as **By User**, with the human as **On Behalf Of User** in delegate mode.
The security overview describes ASOR storing credentials, including a private
key, in a Credential Store and retaining a reference. It describes delegate
authentication as an On-Behalf-Of flow. [Agent Security][wd-security]

**[Workday fact]** External-agent configuration requires an ambient X.509 public
key supplied by the agent platform, unique to the agent. It supplies a delegate
client secret and an ambient ASU username. Delegate skill availability is
configured through security groups, with underlying API permissions also
required. [Configure External Agents][wd-external-current]

**[Workday fact]** The external-ASU guide describes delegate authorization-code
consent with access and refresh tokens. Ambient JWT assertions use the client ID
as issuer and ASU username as subject, signed using the registered key pair.
[External Agent ASU Considerations][wd-external-asu]

**[Lab policy] Evidence reconciliation:** the requested
[external-agent URL][wd-external-requested] failed retrieval twice. The production
page at [its newer path][wd-external-current] was retrieved, as was the
[older-path staging page][wd-external-stage]. The production pages above support
the claims. The general OBO description does **not** establish an external
RFC 8693 endpoint or parameter set. Do not claim that our grant-ID flow, TTLs,
rotation overlap, revocation latency, or AWS secret storage are Workday facts.
The earlier [verification document](workday-verification.md) explicitly left
delegated-agent mechanics unchecked; this evidence supplements that gap.

**[Workday fact]** Workday's June 3, 2025 announcement describes Agent Gateway
connecting partner agents with ASOR using MCP and A2A.
[Workday announcement][wd-gateway]

## 2. Agent identities and the existing grant (A)

### 2.1 Registration and accounts

**[Workday-inspired]** Register an agent independently in each tenant. A
registration has `id`, `ref_id`, `display_name`, `enabled`, and zero to two
mode-specific ASUs. Enabling a mode creates its ASU and OAuth client together.
Disabling the registration disables authentication for both modes, without
deleting history. Reusing the same reference in another tenant creates an
unrelated identity.

**[Lab policy]** Proposed relational additions:

| Record | Fields and constraints |
|---|---|
| `agent_registrations` | Tenant-scoped WID; unique `(tenant_id, ref_id)`; name, enabled, created_at |
| `agent_system_users` | Tenant, registration, account, `DELEGATE` or `AMBIENT`, client, `credential_store_ref`; unique `(tenant, registration, mode)`, account, and client |
| `accounts` extension | `kind=ASU`; no worker, password login, or UI session |
| `api_clients` extension | Optional unique ASU binding; fixed `allowed_operations` for ASU clients; existing `scope_ceiling` retained |
| `credential_versions` | Tenant, ASU, opaque version ID, public-key fingerprint if applicable, secret version reference, activation/accept-until timestamps, revoked_at; no secret material |
| `assertion_uses` | Tenant, client, JWT `jti`, expiry; unique binding for replay prevention |

**[Lab policy]** All new tenant tables have FORCE RLS, explicit tenant predicates,
and composite tenant foreign keys. `mw_app` reads identity configuration and
inserts replay markers; owner-only admin mutations configure identities and
credentials. Expired replay markers may be deleted. Existing audit tables remain
append-only. Specify exact grants in spec §1 at checkpoint 2.

**[Lab policy]** Registration/configuration is initially through the existing
isolated test-admin app, never a new public superuser API. Proposed routes under
`/admin/agent-registrations`: create, enable/disable, configure a mode, update
its scope/operation ceiling, add a credential version, revoke a version.
They use the established admin `slug` convention, return 404 across tenants,
422 for invalid modes/configuration, and 409 for duplicate ASUs/registrations.
No public credential-fetch endpoint. Public token/grant endpoints remain the
integration surface; an operator supplies non-secret client IDs and ASU names.
Admin changes record operator attribution as `test-admin`, not as a verified
human identity. A production tenant-admin permission model is deferred.

### 2.2 Ambient authentication

**[Workday-inspired]** `POST /oauth2/token` adds JWT-bearer authentication for an
ambient ASU. Form: `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`,
`client_id`, `assertion`. No shared client secret for ambient ASUs.

**[Lab policy]** The JWT profile uses [RFC 7523][rfc7523] as a reference, with
these fixed choices, not claims about Workday's exact protocol:

- RS256 only; RSA public key at least 2048 bits in a registered PEM X.509
  certificate. Header `kid` selects a version already bound to this tenant and
  client. Ignore certificate subject/SAN for identity mapping. Do not fetch
  `jku`/`x5u` or accept caller-provided keys as trust roots.
- `iss=client_id`; `sub=ASU username`;
  `aud=https://{slug}.mockworkday.local/oauth2/token`; require `iat`, `exp`, `jti`.
  Maximum assertion lifetime 60 seconds, future `iat` tolerance 5 seconds;
  `exp > now`. Certificate validity and credential acceptance window must hold.
- Reject wrong tenant, subject, audience, mode, disabled identity, expired or
  revoked certificate, bad algorithm/signature, or reused `jti` with 401
  `INVALID_GRANT`; unknown/disabled client gives generic `INVALID_CLIENT`.
  A database uniqueness check consumes `jti` atomically with successful issuance.
- A self-signed certificate may be registered by the authorized operator. It is
  a pinned public key, not a public-CA or mTLS trust claim. No CRL/OCSP machinery;
  revocation is the registered version's state.
- Issue a Mock Workday access token with `typ=ambient`, `sub=ASU account`,
  `client_id`, `agent_id`, `asu_id`, `credential_version_id`, scope/operation
  ceilings, and existing issuer/audience/time/JTI claims. Its maximum TTL is
  300 seconds, capped by credential acceptance and certificate expiry.

### 2.3 Delegate OBO and compatibility

**[Lab policy] Proposed decision:** keep `delegation_grants` as the **only**
human consent record. A grant still binds tenant, human, client, scopes, expiry,
and revocation. ASU delegate clients can be grant recipients; ambient clients
cannot. Do not introduce another consent table, refresh-token database, or
agent-specific grant API.

**[Workday-inspired]** New ASU delegate exchange combines authenticated agent
identity with human authority and returns an attenuated token. Its concrete
form reuses the existing token-exchange grant-type identifier; grant-based
renewal is a lab extension, not a claim of full [RFC 8693][rfc8693] compliance:

| Form field | Proposed meaning |
|---|---|
| `grant_type` | Existing `urn:ietf:params:oauth:grant-type:token-exchange` |
| `client_id`, `client_secret`, `credential_version_id` | Delegate ASU authentication, active version required |
| `grant_id` | Existing consent record; identifies the consenting human and must belong to this tenant/client |
| `scope` | Optional requested narrowing; cannot expand the grant or client ceiling |

**[Lab policy]** The human proves presence once when creating the grant.
Renewal requires delegate ASU client authentication, an active credential version
and the active grant; it requires no fresh human subject token. Reject another
tenant/client's grant, expired/revoked consent, disabled human, or ambient-client
exchange. No chained delegation. Output keeps `typ=delegated`, `sub=human`,
`gid`, and `act.client_id`; add `act.sub=delegate ASU`, agent/ASU/credential
version and operation ceiling. TTL is the minimum of 300 seconds and remaining
grant and credential lifetimes. Every call still checks the human's current
authority. The grant is the lab analogue of a refresh token, with one revocation
record; Workday's documented authorization-code access/refresh flow supports
renewal without human presence [External ASU considerations][wd-external-asu].

**[Lab policy]** Existing unbound `assistant`/`hr-assistant` grant-ID-plus-secret
exchange, ordinary ISUs, synthetic credentials, and issued token meanings stay
compatible. No silent conversion of existing clients into ASUs. New ASU clients
cannot fall back to legacy secret-only exchange or ISU client credentials.
Whether to retire legacy delegation later is a separate decision.

### 2.4 Narrow authority and request validation

**[Lab policy]** For ASU delegates, preserve the human `Principal.account_id`
and worker for spec §4 membership and §5 business-process checks. Add separate
actor ASU fields for attribution. Never substitute the ASU for the human when
checking self/initiator restrictions, assignees, pagination, or idempotency.
Ambient ASUs use the existing integration-group branch of §4.3, with explicit
domain grants; no human memberships and no BP initiation/approval in M3.

**[Lab policy]** A small fixed operation list on the ASU client narrows the
coarse scopes: use named OpenAPI business operations (including reads,
document creation, report export, BP initiation and each action), not URL
patterns or a policy DSL. Every ASU request must pass the operation ceiling
before existing §4/§5 authorization. Omitted operations deny; the token's
operation set is intersected with the current set, never expanded. This models
allowed skills without building a skill catalogue. A finite test case must
show that a human can approve an action but its delegate cannot.

**[Lab policy]** Every call additionally rechecks tenant binding, registration,
ASU, client and credential-version status and windows. Delegate access is the
intersection of current human authority, token scopes, current client ceiling,
current grant scopes, and the token/current operation ceiling. Ambient access
uses current ASU domain/group authority and token/current ceilings. Existing
grant/account/client disabling rules still apply. Revocation is effective for
the next authentication check, including unexpired tokens and idempotent
replays; it cannot cancel a transaction already authorized and in progress.
No positive cache may bypass these checks. Scope growth requires a new token.

## 3. Credential Store and audit (H, A)

### 3.1 Ownership and storage

**[Lab policy] Proposed external-agent model:** separate the authenticating
party's secret custody from Mock Workday's credential verification. This avoids
sharing an AWS secret or granting either service access to the other's storage.

| Material | Owner/location | Mock Workday record |
|---|---|---|
| Ambient signing private key | External platform's Secrets Manager, encrypted with its KMS key; only its trusted signer may read it. Local test signer uses a generated, gitignored mode-0600 file | Public certificate, fingerprint and version reference only; never upload the private key |
| Delegate shared client secret | Generated on enrollment; delivered once over an authorized encrypted operator channel; external platform stores its copy in its Secrets Manager/KMS | Verifier hash in Mock Workday's Secrets Manager credential document |
| Ambient certificate and delegate verifier versions | Mock Workday Secrets Manager, one secret per ASU, encrypted with that tenant's customer-managed KMS key | ASU's `credential_store_ref` plus non-secret version/status metadata; no credential payload in registry |
| TLS leaf key | Mock Workday operational Secret and imported ACM certificate (§5) | ARN/reference only |
| Mock Workday access-token signing key | Existing issuer key mechanism in spec §3 | Independent of ASU and TLS credentials; redesign deferred |

**[Lab policy]** This implements a Secrets Manager-backed ASU **verification**
store; it does not pretend to reproduce Workday's internal custody of keys for
its managed agents. Approve this distinction explicitly (§10). There is no
reason for this service to hold an external ASU's private signing key. AWS KMS
encrypts secret storage; it does not make an exported signing key non-exportable.
The external signer holds plaintext only in its trusted process while signing.

**[Lab policy]** Tenant-tagged role sessions (§4.1.1) may read only that tenant's
ASU secrets; creation/version writes belong to the admin/bootstrap role. The
base app role cannot read tenant secrets directly. Key policy
adds tenant-specific Secrets Manager access conditions (`kms:ViaService` and
the secret encryption context). No key, secret, token, assertion, password,
signed URL, or private interview context is committed or logged. Terraform
creates secret containers and permissions; a post-approval enrollment operation
writes secret values without placing them in Terraform variables/state.
Unavailable secret/KMS verification fails closed with 503 `SERVICE_UNAVAILABLE`.

**[Lab policy]** Local pytest/Compose remain AWS-free: a private local credential
document mirrors the secret payload and version references. Use two direct
storage functions with an explicit AWS/local branch, no provider registry.
Tests generate credentials; seeded registration IDs are deterministic but keys
and secrets are not. No public endpoint returns stored secret material.

**[Lab policy — slice 2b implementation, owner-enrollment split approved]** `MW_CREDENTIAL_STORE` is `aws` or a local mode-0600 file path. An owner-only `python -m mock_workday.credentials` command writes versions; its exclusive mode-0600 output delivers the delegate secret once. The isolated admin API activates `existing_version` in AWS; it never writes AWS secrets. Version identifiers use UUID hex strings. Local revocation removes material after the audited database commit; AWS obsolete versions require owner cleanup. Explicit `python -m mock_workday.identity` seeds disabled registrations/ASUs separately from the stable fixtures; AWS requires a `--references` JSON map of tenant slug to mode to provisioned secret ARN. See main spec §3.6 and README for the implemented contract.

### 3.2 Rotation and revocation

**[Lab policy]** Stable ASU/client IDs survive rotation. Permit at most two
accepted credential versions per ASU. Add and verify a replacement, then set
the old version's `accept_until=now+300s`. Both work during that fixed overlap;
afterward only the new version works. A failed addition leaves the old version
unchanged. Registration of the same public key to another ASU is rejected
(global fingerprint uniqueness enforced by owner; generic conflict response).

**[Lab policy]** Secrets Manager version IDs select immutable credential
payloads; database version status is the authority for acceptance. Do not infer
revocation from `AWSCURRENT`/`AWSPREVIOUS` staging labels. Write the secret version
first, then commit its active reference and audit; a failed DB commit leaves an
inert version for admin cleanup, never an automatically accepted credential.
Revocation first commits `revoked_at` with audit, then removes obsolete secret
material where possible. Stale secret versions cannot reauthorize a revoked
credential. Lost one-time enrollment responses require rotation, not retrieval.

**[Lab policy]** Emergency revocation has no overlap. Disabling an agent, ASU,
or client blocks all its versions. Audit records version IDs/fingerprints and
times, never secret hashes or values. Rotation uses existing controllable time;
real Secrets Manager/KMS availability and AWS certificate validity are tested
against wall time during AWS smoke tests. Set the controllable clock to current
UTC before live ASU enrollment; historical seed dates remain unchanged.

### 3.3 Audit attribution and logs

**[Workday-inspired]** Add explicit `by_user_account_id` and
`on_behalf_of_user_account_id` to authorization/object/BP history records:

| Caller | By User | On Behalf Of User |
|---|---|---|
| Human | Human | null |
| Ordinary ISU | ISU | null |
| ASU ambient | Ambient ASU | null |
| ASU delegate | Delegate ASU | Human |

**[Lab policy]** Preserve existing human account/client/grant fields and
semantics. Legacy unbound delegates retain their old attribution and are marked
`legacy_delegated`; do not invent an ASU for them. Add agent, ASU, credential
version, and `X-Request-Id` to new ASU records. Token issuance and credential
administration get tenant-scoped append-only identity audit. Successful issuance
requires its audit commit; existing §8 fail-closed sensitive read/write and
best-effort denial rules remain.

**[Lab policy]** Emit structured JSON on stdout to CloudWatch Logs, **3-day
retention**, log group removed with D1 service teardown. Fields: wall-clock
timestamp, level, service, `request_id` (the echoed `X-Request-Id`), tenant WID,
HTTP method and route template, status, duration, error code, actor/client/ASU
IDs, and event/export IDs when relevant. No raw query strings, headers, bodies,
salary values, document content, or URLs with signatures. JSON encoding prevents
log-line injection; request IDs are correlation only and grant no authority.
Encrypt the log group with the operational KMS key. Database audit remains the
business record; logs are troubleshooting evidence, not its replacement.

**[Lab policy]** Audit querying remains test-admin/operator-only in M3. Runtime
integration correlates its HTTP observations using request IDs; no database
access is granted. A public audit-report domain is deferred.

## 4. Tenant data, synthetic seeds, and reports (C, I, K, L)

### 4.1 Storage and isolation

**[Lab policy]** AWS stores document bodies and report objects in **one S3
general-purpose bucket per tenant**, S3 Standard. Prefixes are
`tenants/{tenant_wid}/documents/{document_wid}` and
`tenants/{tenant_wid}/exports/{export_wid}.ndjson`. The service derives
bucket/key from the authenticated tenant and server-created IDs; callers cannot
provide either. PostgreSQL continues to own worker/compensation data, document
metadata, permissions, and audit. This is not an S3 database migration.

**[Lab policy]** Use SSE-KMS with **one customer-managed symmetric key per
tenant** and enable **S3 Bucket Keys**. Block public access, disable ACLs with
Bucket Owner Enforced, require TLS and the exact tenant key on writes. Bucket
policies and tenant-session IAM restrict reads/writes to approved Mock Workday roles;
runtime roles have no list/get/decrypt access. The key policy is a second
authorization layer: permit use through regional S3 for that bucket's encryption
context and through Secrets Manager only for that tenant's ASU secrets.
Bucket Keys use the **bucket ARN**, not the object ARN, as S3 encryption context.
[S3 security][aws-s3-security], [SSE-KMS][aws-s3-kms], [Bucket Keys][aws-bucket-keys]

**[Lab policy]** A shared trusted app can access all configured tenants; separate buckets
and keys do not contain compromise of that app. HTTP host resolution, explicit
tenant predicates, RLS, and server-derived storage addressing remain essential.
Test both application isolation and IAM/key-policy rejection of wrong resources.

**[Lab policy]** KMS use, including decrypt calls, is visible in CloudTrail.
Bucket Keys reduce KMS request cost and also mean there is not a KMS decrypt
event for every object read. Application audit records authorized content reads.
Use existing CloudTrail management-event visibility for this disposable lab;
do not modify a platform trail. S3 access logs/data events and request metrics
are recommended for a longer-lived environment, but dedicated paid collection
is deferred to keep this resource set small. [KMS and CloudTrail][aws-kms-trail]

**[Lab policy]** Ordinary documents remain text-only, 64 KiB maximum, with the
same POST/GET/list shapes, domains, classifications, redaction and authorization
as spec §§6–7. Lists never read or return object bodies. No presigned document
URLs. Authorize before S3 access; sensitive reads commit audit before response.
Missing/corrupt objects or S3/KMS failure return 503 `SERVICE_UNAVAILABLE`, not
an empty successful document. Store byte length and SHA-256 with metadata.

**[Lab policy]** For create, authorize, write an immutable object, then insert
metadata and audit in the DB transaction. On rollback, best-effort delete the
object; preserve it on an uncertain DB commit outcome because metadata may
already be durable. A crash may leave an inaccessible orphan. Do not promise a transaction
across PostgreSQL and S3. An operator cleanup compares object IDs against DB
metadata; teardown empties all objects. No queue or background worker is added.
Local mode retains DB text bodies behind the same document functions, with
AWS client stubs for failure tests and real S3 checks only at checkpoint 4.

### 4.1.1 Tenant-tagged STS sessions (L)

**[Lab policy]** Add one service-owned tenant-data role. The base ECS task role
has `sts:AssumeRole`/`sts:TagSession` for that role but no direct tenant S3,
Secrets Manager, or KMS data permissions. Trust only the exact Mock Workday task
role, require `aws:RequestTag/tenant` to be a provisioned tenant WID, require
`Null: {"aws:RequestTag/tenant": "false", "aws:TagKeys": "false"}`, and constrain
`ForAllValues:StringEquals` on `aws:TagKeys` to `["tenant"]`. Both
`sts:AssumeRole` and **`sts:TagSession`** must be authorized in the trust policy.
Do not accept caller-provided AWS role ARNs or session tags. The app obtains the
WID from authenticated Host resolution, then attaches `tenant=<wid>`.
[STS session tags][aws-session-tags]

**[Lab policy]** Tenant-data role S3 permissions bind bucket and prefix to
`${aws:PrincipalTag/tenant}`: bucket names include the tenant WID, and object
ARNs include `tenants/${aws:PrincipalTag/tenant}/*`. `ListBucket` is limited by
`s3:prefix`; object read/write/delete policies use object ARNs, not a prefix
condition unsupported by that action. Missing/mismatched tags deny. No broad
bucket-policy grant may bypass the role restriction. KMS key policies require
`aws:PrincipalTag/tenant` equal to that key's fixed tenant WID as well as the
service/encryption-context restrictions in §4.1; no task-role direct decrypt
allow. Tenant secret read policy has the same tag/tenant binding. Test with
real session credentials, including wrong/missing tags; application tests alone
cannot prove IAM isolation. Bootstrap has a separate operator-authorized role
and is never used as an application fallback.

**[Lab policy]** Assume 900-second sessions and cache credentials in memory by
`(role_arn, tenant_wid)` only, refreshing with 120 seconds remaining. Lock cache
refresh per tenant; never share an S3 client using another tenant's credentials.
No disk cache, logs of credentials, or transitive tags/role chains. A disabled
tenant is checked before every cloud call, regardless of cache freshness.
Presigned exports use the matching tenant session and cannot outlive it. A
compromised trusted service can still select any configured tenant; ABAC limits
accidental cross-tenant cloud calls, not compromise of the trusted tag issuer.

### 4.2 Deterministic realistic seed generator (K)

**[Lab policy]** Keep the exact Acme/Globex small fixtures in spec §10 as the
fast test default: no extra workers or changed IDs, roles, vacancies, history,
or pending requests in those tenants. Add a separate `bulk-v1` seed command
that loads those fixtures **plus** three synthetic tenants. This replaces the
initial hand-sized M3 expansion; it does not replace the regression fixtures.

| New tenant | Workers | Supervisory organizations | Positions | Documents |
|---|---:|---:|---:|---:|
| `northstar` | 512 | 43 | 600 | 2,048 |
| `meridian` | 1,024 | 85 | 1,200 | 4,096 |
| `cedar` | 2,048 | 171 | 2,400 | 8,192 |
| **Bulk additions** | **3,584** | **299** | **4,200** | **14,336** |

**[Lab policy]** Use Python's local `random.Random(20261008)` with generator
version `bulk-v1`, sorted tenant/entity iteration and separate deterministic
per-tenant streams derived with SHA-256 (never Python `hash()`). WIDs retain
spec §2's UUID5 scheme. Pin generation order and templates; record per-file
hashes and counts in a manifest. A fixed RNG alone is insufficient if code,
iteration order, or locale changes. Dates are anchored to spec's seed date;
secrets and key pairs are generated separately and never deterministic.

**[Workday-inspired]** Produce a 4–5-level supervisory tree with executives,
divisions, departments and teams; managers occupy positions in their parent
organization. Names, job titles, locations mentioned in documents, tenure,
promotions, and salary bands are synthetic but mutually consistent. Use only
the existing API data fields; narrative realism does not imply new worker
schema fields. Include manager, HR and compensation roles with constrained
reach and a few deliberately unfilled leadership positions to exercise pruning.

**[Lab policy]** Every bulk worker has three job revisions and three
compensation revisions, effective 2024-01-01, 2025-01-01 and 2026-01-01. Allocate
historical positions without overlapping occupants and reserve vacant positions
for live Change Job tests. Salary bands follow synthetic role/seniority ranges
(USD 45,000–280,000), with positive, internally consistent progression. Every
worker has a synthetic human account. Bulk accounts share the documented
synthetic password `pw-bulk-{slug}`; compute one password hash per tenant and
reuse it for that tenant's accounts, avoiding 3,584 expensive KDF calls. Original
small-fixture passwords and hashes retain their existing behavior.
Add one registration/two ASUs per tenant; ambient defaults to staffing reads,
delegate to limited staffing/absence/documents operations; AI and export use
require explicit entitlements. Seed no usable long-lived ASU secret.

**[Lab policy]** Generate two time-off requests and one completed Change Job
per bulk worker, with consistent events, steps and history: 10,752 events total.
Time-off statuses cycle across completed, denied, canceled and in-progress;
completed job events link to historical revisions. Pending job changes, if used
in targeted tests, are created by that test, not bulk load. Seeding runs as owner
and suppresses EventBridge/Bedrock calls. It is a fixture import, not live
business transactions or proof of historical approvals.

**[Lab policy]** Add a narrow read-only `time_off_balances` snapshot table for
bulk data: tenant, worker, plan=`VACATION`, as_of date, granted/taken/remaining
hours in quarter-hour units. `GET /api/v1/workers/{id}/time-off-balances?as_of=`
returns the latest snapshot no later than the date, authorized by current
`ABSENCE` VIEW plus `absence` scope for scoped callers. Snapshots have FORCE RLS
and owner-only writes. Values agree with completed seeded requests at load time.
Live Request Time Off **still does not debit balances** (spec §5.4); the endpoint
labels its `asOf` and does not claim a live accrual engine. This is a proposed
contract addition, not an undocumented change to existing BP semantics.

**[Lab policy]** Each worker gets four documents: offer letter, performance
note, onboarding note and policy acknowledgement. Replace selected repeated
acknowledgements within the fixed totals with tenant handbooks, org plans and
benefits/security/leave policies. Targets/classification follow spec §7. Body
sizes cycle deterministically from 4 to 16 KiB (about 10 KiB average): roughly
140 MiB of document bodies. Budget under 0.5 GiB DB storage including indexes
and metadata in AWS, under 0.7 GiB locally with text bodies. These are design
estimates. The realistic bulk-v1 revision measured 146,761,728 body bytes and
72,584,339 local DB bytes, retaining exact body byte targets.
Cloud sizing/timings remain checkpoint-4 evidence.
No binary parsing, embeddings, or bulk report objects are precomputed.

**[Lab policy]** At least 1% of documents carry tagged manifest fixtures for
forged administrator instructions, salary disclosure, cross-tenant references,
and calls to `https://exfil.invalid/collect`; include benign matched controls
and keep existing injection fixtures unchanged. Use varied paraphrases and
quoted-policy examples. Labels remain in the fixture manifest, not safety
claims returned by the API. Content remains data; neither the document service
nor native AI may perform actions or fetch URLs in it.

**[Lab policy] Loading:** `make up` and ordinary `make test` retain small fixtures.
A proposed `make seed-bulk` invokes the same deterministic generator/importer
locally; `make test-bulk` checks manifest counts, isolation and sampled history
in a throwaway Postgres cluster, with no AWS or Docker requirement. In AWS,
the existing one-off bootstrap task runs `bulk-v1` against RDS and uploads S3
bodies in bounded batches (100 rows, at most 8 concurrent object writes).
Use no internal queue. Write objects before committing metadata; record a seed
version/checksum per tenant. On rerun, skip verified matching objects and rows;
a manifest/version mismatch fails instead of overwriting modified data.
Reset is an explicit destructive admin operation, never implicit on app startup.
Partial imports are unavailable until the tenant's load marker is complete;
resume safely or reset that synthetic tenant. Preserve deterministic IDs across
local/AWS loads, print no credentials, and inventory orphan objects on failure.

### 4.3 Synchronous report export

**[Workday-inspired]** Add one fixed report, `worker-roster`, exported through
`POST /api/v1/report-exports` with `{report: "worker-roster", as_of?,
include_compensation: false}`. No arbitrary SQL, template engine, document-body
export, or background report job. The handler reads a consistent DB snapshot,
applies existing current-authority row and field filtering, and streams NDJSON
to a temporary private file then S3. Cap at 10,000 visible rows / 16 MiB; exceed
either → 422 with no successful export. This is larger than paginated API
responses while fitting the existing small service.

**[Lab policy]** Rows use worker-reference/organization/position fields from
the API, with compensation only when requested **and** authorized per row.
Require `staffing` plus the export operation for ASUs, and `compensation` for
scoped callers requesting compensation. Hidden workers are omitted; omitted
salary fields are absent, never fabricated. Direct humans use their existing
authority. Creation is a sensitive disclosure: audit must commit before any
download capability is returned. Record report, filters, row count, checksum,
object ID, actor, and grant; no contents or signed URL. No idempotency guarantee
for exports; a retry may produce another snapshot.

**[Lab policy]** Return 201 with `{id, report, row_count, byte_length, sha256,
created_at, expires_at, download_url}`. In AWS, `download_url` is a GET-only
presigned regional S3 HTTPS URL for that exact object, **60 seconds** maximum,
also capped by remaining caller token/grant/credential and signing AWS session
lifetime. Use wall time for AWS signatures and make `expires_at` truthful even
when the test business clock is frozen. No list capability, uploads, or bucket
credentials. Local mode uses a 60-second signed HTTP download capability on the
same public app, with the same response shape and bearer semantics.

**[Lab policy] Explicit exception to C:** documents stay API-only; report
creation and authorization are API-only, but the resulting report bytes are
downloaded directly from S3. This is the reviewer-requested exception to the
original storage boundary and needs incorporation into the main contract.
The URL is a **bearer capability**: anyone holding it can download its snapshot,
potentially repeatedly, until expiry. S3 cannot recheck a delegation grant or
role revocation. Revoking the grant does not instantly invalidate an issued
URL; deleting the object or denying S3 access can block future requests.
An in-progress download may continue past expiry. Never put URLs in logs,
events, or audit. [S3 presigned URLs][aws-presign]

**[Lab policy]** Export objects are not versioned; expire each `tenants/{wid}/exports/` prefix
after one day as a storage backstop (lifecycle is asynchronous, not the access
TTL). Documents have no time-based expiry. Empty both prefixes on teardown.
No new URL-renewal endpoint: request and authorize a new report. Report URLs
require ordinary AWS S3 server trust and an HTTPS S3 network path; they do not
traverse Mock Workday PrivateLink. Runtime egress policy must permit this narrow
download or the runtime must defer use of reports. No shared S3 permissions.

## 5. Private connectivity, DNS and TLS (D, F)

**[Lab policy] Proposed topology:**

```text
Runtime trusted HTTP client
  -> tenant DNS -> consumer interface endpoint :443
  -> Mock Workday PrivateLink endpoint service
  -> internal NLB TLS :443 -> ECS HTTP :8080

Operator /32 -> existing public ALB HTTP :80 -> ECS :8080
Operator ECS Exec -> localhost :8081 (test admin only)
```

**[Lab policy]** Mock Workday owns an internal NLB, TLS listener, IP target
group for the existing ECS task, endpoint service, allowed-principal permissions,
and explicit endpoint acceptance. Enable two provider AZs and NLB cross-zone
forwarding so one D1 task can serve either endpoint AZ; include transfer usage
in cost. No public NLB address, NAT, or new provider VPC. Keep D1 public task
placement for image pulls and AWS API egress. Task ingress admits the ALB and
NLB security groups on 8080 only; no load balancer reaches 8081 or RDS.
[PrivateLink provider/consumer prerequisites][aws-privatelink]

**[Lab policy]** Runtime owns its interface endpoint, endpoint security group,
and trusted-client egress rules. Start with one consumer AZ for cost, knowingly
without HA. Allow only that consumer's approved AWS principal to request a
connection; accept only the approved endpoint ID. Neither PrivateLink nor an
IP address is tenant authentication: normal Host/JWT authorization still runs.
No wildcard allowed principals. If NLB inbound evaluation for PrivateLink is
disabled, keep direct NLB ingress closed and rely on approved endpoint
connections plus consumer endpoint SGs; record that choice in the plan.

**[Lab policy]** Publish a non-secret deployment handoff: endpoint service name,
provider AZ IDs, required port, tenant hosts, CA certificate/fingerprint, and
API/OpenAPI version. Consumer supplies its endpoint ID/DNS target and VPC
association details. This is configuration exchange, not cross-repo Terraform
state access. The runtime repository is read-only for this checkpoint and does
not receive automated edits or resources from Mock Workday.

**[Lab policy]** Mock Workday owns one Route 53 private hosted zone,
`mockworkday.internal`. Exact records for `acme`, `globex`, `northstar`,
`meridian`, and `cedar` under that suffix resolve to the consumer interface endpoint
DNS target, with TTL 60 seconds for CNAME records. Associate the zone with the
approved consumer VPC; cross-account association requires actions by both
owners. Do not enable PrivateLink's provider private-DNS verification for the
reserved `.internal` suffix. Runtime clients use their VPC DNS resolver,
and smoke tests must resolve these exact names; canonical `.local` identity
strings do not select private DNS names.
No wildcard tenant records. [Private hosted zones][aws-private-dns]

**[Lab policy]** Generate a small disposable lab CA offline and a server leaf
certificate whose SANs contain all five tenant hostnames. CA private key is an
operator-only mode-0600 file, never a service credential or repository file.
Leaf key is stored in the operational Secret and imported into ACM for NLB TLS
termination; imports occur only at approved deployment, outside Terraform secret
values. Inventory the resulting ACM ARN for teardown. No AWS Private CA.
Leaf validity 30 days, CA validity 90 days, both wall-clock based; regenerate
per disposable environment. The runtime installs **only the public lab CA** in
its trusted client's CA bundle, verifies hostname and validity, sends matching
SNI/Host, and never disables certificate checks. It must trust Mock Workday JWKS
separately for token validation; the CA is not a token signing key.

**[Lab policy]** TLS covers consumer to provider NLB; the NLB-to-task hop uses
D1 HTTP inside the provider VPC, isolated by security groups. This is not
end-to-end TLS to the process; approve this deliberate boundary or request a
separate backend-TLS design before checkpoint 3. Operator access remains D1's
/32-restricted HTTP ALB and ECS Exec; do not pass new private credentials over
that HTTP test path. Certificate enrollment uses the operator channel.
Issuer/audience URLs remain those in spec §3; no new tenant naming scheme.

### 5.1 Minimal public WAF (N)

**[Lab policy]** Attach one Regional WAFv2 web ACL to the **public ALB only**.
Use `AWSManagedRulesCommonRuleSet`, `AWSManagedRulesKnownBadInputsRuleSet`, and
one source-IP rate rule: 2,000 requests in a five-minute evaluation window,
blocking excess requests. This is approximate abuse control, not the tenant
quota or authorization system. Keep the ALB /32 restriction and all app checks;
do not trust a caller-supplied forwarded IP header for the rate key. PrivateLink
does not traverse WAF. [AWS baseline groups][aws-waf-groups]

**[Lab policy]** Preserve valid 64-KiB document submissions: override only the
Common Rule Set `SizeRestrictions_BODY` to Count; the app enforces its explicit
limits. ALB WAF inspects at most the first 8 KiB of a body and is not a full-content
safety check ([AWS inspection limit][aws-waf-body]).
Test legitimate JSON documents and injection fixtures against managed rules;
any further narrowly scoped override must be explained in the implementation
decision log, never a broad allow bypass. No Bot Control, CAPTCHA, Fraud Control,
Marketplace rules, Shield Advanced or CloudFront. Disable sampled requests to
avoid credential/body capture; use managed metrics and redacted app logs, not
full WAF request logs. WAF blocks may never reach the app or have its JSON error
shape/request ID. Budget $8/month fixed plus $0.60/million requests (§8).

### 5.2 Conditional public DNS and TLS (O)

**[Lab policy]** Terraform input `public_domain` defaults to null. When absent,
keep D1 HTTP and operator Host-header access. When supplied, it must be a domain
or dedicated subdomain the user controls at an **external registrar**; Route 53
Domains purchases are blocked and not part of the workflow. Create one public
hosted zone, publish its NS delegation instructions, and wait for actual DNS
delegation before claiming readiness. Do not change registrar settings or an
existing parent zone automatically.

**[Lab policy]** Create explicit `{slug}.{public_domain}` alias records for all
seeded tenants and an ACM **non-exportable public** certificate for
`*.{public_domain}` in Ohio. DNS-validation records remain for renewal. ALB 443
uses the certificate and port 80 redirects to HTTPS; keep /32 and WAF. No wildcard
DNS record and no apex service are needed. Unknown hosts still fail closed.
[ACM DNS validation][aws-acm-dns], [ACM pricing][price-acm]

**[Lab policy]** Application input `MW_PUBLIC_DOMAIN` mirrors that value. It
adds exact host aliases for known tenants; it does not derive arbitrary tenant
slugs from an unvalidated suffix. Keep the existing `.mockworkday.local` issuer,
API audience and assertion audience as canonical identifiers for both paths,
so existing/private tokens remain compatible. Canonical issuer strings need
not be the transport address; document JWKS retrieval over the selected trusted
base URL. The public browser/client uses public CA trust; private clients retain
the lab CA. Private DNS/leaf SANs extend to all five tenants. Never redirect
OAuth POST bodies across hosts; clients select the HTTPS base URL first.

**[Lab policy]** Teardown removes the service-owned public zone/records and ACM
certificate, reporting any external NS delegation left behind. The registrar's
domain is user-owned, not deleted; registration charges are external. The next
deployment may produce new nameservers requiring redelegation. No domain is
needed to finish checkpoints 1–3; the null path is fully specified.

## 6. Business events and provider dead letters (G)

**[Workday-inspired]** Publish notifications for committed business-process
changes. **[Lab policy]** EventBridge and SQS are lab transport choices; no
claim is made about Workday's internal event system or AWS infrastructure.
The separate versioned event schema is an explicit addition to the current
HTTP-only integration boundary. HTTP remains the sole authority for reads and
mutations; an event is a notification, not a command or permission.

### 6.1 Schema and semantics

**[Lab policy]** Source `lab.mock-workday`; EventBridge `detail-type`
`MockWorkday.BusinessEvent.v1`. Example detail (WIDs abbreviated for readability):

```json
{
  "schema_version": 1,
  "event_id": "stable-uuid-for-this-transition",
  "tenant_id": "tenant-wid",
  "tenant_slug": "acme",
  "event_type": "job_change.approved",
  "occurred_at": "2026-10-08T09:00:00Z",
  "business_process_event_id": "bp-wid",
  "business_process_version": 3,
  "status": "SUCCESSFULLY_COMPLETED",
  "subject_worker_id": "worker-wid",
  "effective_date": "2026-11-01",
  "by_user_id": "account-wid",
  "on_behalf_of_user_id": null,
  "client_id": null,
  "request_id": "echoed-X-Request-Id",
  "resource_href": "/api/v1/business-process-events/bp-wid"
}
```

**[Lab policy]** M3 types: `job_change.submitted` after initiation,
`job_change.approved` after **final** approval, and `time_off.approved` after
final approval. Intermediate approval, cancellation, and denial notifications
are deferred. Future-effective job approval is not a claim the job is already
effective. Tenant and object identifiers come from committed server state,
never caller-supplied event bodies. `occurred_at` uses business time; transport
time is separate wall time. No compensation values, free-text reasons,
document content, credentials, URLs with signatures, or executable instructions.

**[Lab policy]** Record a stable transition ID and version in existing
transactional BP history. Re-publication reconstructs the same detail from
that committed history, including actor attribution and original request ID;
EventBridge's envelope ID may differ. Consumers deduplicate by
`(tenant_id, event_id)`, tolerate duplicates and out-of-order delivery, and use
BP version to recognize stale notifications. Version gaps are valid because
not every transition emits a notification. Re-fetch through HTTP with current
authority instead of inferring current state from arrival order.

### 6.2 Transactional outbox and publication

**[Lab policy]** Insert an `event_outbox` row in the same database transaction
as the BP transition/history, mutation, audit and idempotency receipt. The row
contains tenant, stable event ID, schema version, immutable event detail,
created time, attempt count, next-attempt time and nullable published time.
Tenant predicates and FORCE RLS apply; grant the application insert/select and
updates only to delivery bookkeeping. A rollback leaves no publishable event.

**[Lab policy]** One small dispatcher loop in the same service image scans
committed due rows by tenant in bounded batches (at most 10). Use row locking
with `SKIP LOCKED` while publishing/marking a batch to avoid overlapping dispatch.
Holding claimed row locks across PutEvents is a deliberate small-service trade-off,
bounded by SDK retries/timeouts. Lease-then-publish is the larger-scale alternative
with shorter transactions but additional lease/recovery logic. A top-level
Exception guard logs only the exception class and keeps the dispatcher running.
Inspect every `PutEvents` entry result. Mark only accepted entries published;
retry failed or unknown outcomes with exponential backoff capped at 60 seconds.
SDK timeouts bound each attempt. A crash after acceptance but before marking
causes republication with the same stable event ID: delivery is at least once,
with duplicates and no global ordering guarantee. Persist failures/retry state
and expose overdue unpublished rows in structured logs. No separate
queue, worker framework or dispatcher deployment is introduced.

**[Lab policy]** This closes the DB/event dual-write loss gap. Eventual publication
still depends on restoring failed infrastructure and keeping the outbox until
accepted; target retry exhaustion remains recoverable through the target DLQ.
Keep a bounded, audited operator history-range republish command as a repair
tool, preserving stable IDs. It is not the normal delivery mechanism. Published
outbox rows can be pruned after 7 days; never prune unpublished rows by age.

**[Lab policy — slice 2c implementation]** Retry delays are fixed at 1, 2, 4, 8, 16, 32, then 60 seconds. The executable starts one dispatcher thread; the app factory/seed tools do not. Absent `MW_EVENT_BUS_ARN`, dispatch keeps a bounded 1,000-detail local capture. Owner repair selects at most 100 history transitions over at most seven days; owner pruning only removes published rows older than seven days. See main spec §5.7 for the implemented commands and schema artifact.

### 6.3 Cross-domain target and DLQ ownership

**[Lab policy]** Mock Workday owns a custom EventBridge bus and one rule/target
per approved consumer. Default target is the other domain's event bus ARN,
same Region; support different AWS accounts through a narrowly scoped sending
role and receiver resource policy. The receiver owns forwarding to its SQS
consumer queue, its queue permissions, retention, and consumer implementation.
That queue is an expected interface only, never a resource in this repository.
[Cross-account EventBridge][aws-cross-account-events]

**[Lab policy]** Match source, detail type, and an explicit tenant allowlist per
consumer target. Receiver checks sending account/source, schema, and allowed
tenant independently. The app role may `PutEvents` only to its own bus. The
sender role may put only to the approved target bus; its EventBridge trust is
limited to the rule/account. Events grant no ability to access S3, fetch data,
obtain a token, or revive a revoked grant. A tenant field is routing metadata,
not proof that the receiving caller may act for that tenant.

**[Lab policy]** Each cross-domain target has its own **Mock Workday-owned
standard SQS DLQ in us-east-2**, with 14-day retention and SSE-KMS using the
operational key. Explicit queue policy permits `events.amazonaws.com` to
`sqs:SendMessage` only from the rule ARN/account; KMS policy permits the needed
EventBridge `GenerateDataKey`/`Decrypt` use for that queue. Operators alone can
receive/delete; the runtime cannot drain it. Set target retry limits explicitly
to 24 hours / 185 attempts. Some permanent errors go directly to DLQ.
[EventBridge retries][aws-event-retries], [DLQ requirements][aws-event-dlq]

**[Lab policy]** Preserve failure attributes, target/rule ARN, error/retry
information, event ID, and tenant for investigation. Add three action-free
CloudWatch alarms per target: visible DLQ messages >0, oldest age >1 hour, and
`InvocationsFailedToBeSentToDLQ` >0. Inspect alarms/metrics in smoke and cleanup;
no SNS. Operator redrive republishes only after fixing the cause, retains the
detail event ID, and deletes a DLQ message only after accepted republication.
An unknown publish result leaves it for retry. This can duplicate delivery to
other matching targets, which is covered by the contract. No automatic redrive.

**[Lab policy]** The provider DLQ covers delivery **to the target bus**, not
downstream processing after that bus accepts it. The runtime team owns failures
between its bus and its queue/consumer. Logs distinguish producer failure,
target failure, DLQ failure, and accepted delivery; none means business action
by the receiver succeeded. A nonempty DLQ must be reported before teardown;
record synthetic diagnostic evidence, then destroy it as part of the approved
disposable session. It is not a persistent exception to `aws-down`.

## 7. Native AI, metrics, and contract changes (J, M)

### 7.1 Tenant-scoped native AI (J)

**[Workday fact]** Workday's developer-toolset announcement describes Illuminate
AI Widgets with developer-defined prompts and expanded AI Gateway APIs for
natural-language report questions and document understanding. Its Illuminate
brief describes prebuilt Workday and AWS AI APIs for Extend apps.
[Developer-toolset announcement][wd-ai-tools], [Illuminate brief][wd-illuminate]

**[Workday-inspired]** Provide native worker/team summaries, document Q&A, and
authenticated model access as Workday-core API features. The brief also lists Workday Assistant among core HCM AI features. Public material does
not establish an unrestricted customer LLM proxy, its exact permissions, or its
backend. Our Bedrock choice, APIs, model and metering below are all **lab policy**.
This service runs fixed inference requests; no customer agent code, tool loop,
model-selected action, or new workflow engine.

**[Lab policy]** Public, synchronous endpoints, all under `/api/v1/ai`:

| Endpoint | Input and behavior |
|---|---|
| `POST /worker-summary` | `worker_id`, optional `as_of`, `include_compensation=false`; existing worker visibility and compensation field permission |
| `POST /team-summary` | `org_id`, optional `as_of`, `include_subordinates=false`, `include_compensation=false`; summarize up to 50 authorized workers; 422 if the visible set exceeds 50, never silently claim a complete team |
| `POST /document-qa` | `question`, 1–8 explicit `document_ids`; authorize each before reading bodies, then answer only from their permitted text; no URL fetch or unbounded tenant search |
| `POST /generate` | `model="mw-small-text-v1"`, `prompt`; controlled text completion for authorized clients, no server-side data retrieval, system-role input, tools, files or arbitrary model/provider URLs |

**[Lab policy]** Add scope `ai` and domain `AI_USE` with VIEW entitlement to
invoke, evaluated against a tenant-wide target. Seed VIEW for ALL_EMPLOYEES and
one explicit unconstrained integration group `Native AI Callers`, initially
empty until an operator assigns test ISU/ASU accounts. Granting it does not grant
worker/document/compensation access. ASU operation ceilings distinguish all four
endpoints. Summary/Q&A also require the relevant existing scopes for scoped
callers. Delegate grants must include `ai` and those data scopes. Direct humans
still require AI_USE plus underlying object/field authority. Deny before a
model call; workers/documents retain existing non-enumerating 404 semantics.
No AI endpoint modifies enterprise data.

**[Lab policy]** Reuse current-authority authorization functions to assemble a
fresh context for each request. Query only the tenant's objects, discard hidden
rows, remove unauthorized compensation **before prompt assembly**, and read
S3 bodies only through that tenant's tagged session. Explicit Q&A document
denial fails the whole request rather than leaking which documents exist.
Document content follows document permissions: a salary already copied into an
authorized document remains the known spec §7 composition gap, not automatic
field-level redaction of free text. `/generate` sends caller-supplied text only;
it cannot police facts the caller already knows or claims.

**[Lab policy]** Model allowlist contains exactly logical alias
`mw-small-text-v1` → **`us.amazon.nova-micro-v1:0`**, Standard tier through
synchronous Bedrock Runtime **Converse**, explicit `maxTokens=512`, temperature
0, no tools, streaming, prompt cache or automatic provider fallback. Limit the
entire assembled input (including fixed prompt and context) to 64 KiB UTF-8;
return 422 if over budget. Delimit documents as untrusted quoted data with
server-assigned source IDs; instructions in them never change authorization,
model, budget or destinations. Responses are advisory text plus server-generated
source references, model alias, invocation ID, input/output usage and request ID.
Do not treat model-authored links or citations as validated references.
If a team summary has no visible source rows, return a fixed no-source result
with empty references/zero usage without calling the model. Q&A rejects empty
source lists and instructs the model to abstain when supplied text lacks an
answer; it never expands access to fill missing context.

**[Lab policy]** Mock Workday's own task IAM role invokes Bedrock without keys.
Allow only `bedrock:InvokeModel` for the selected profile and exact Nova Micro
foundation-model ARNs in its listed destinations, conditioned to the profile
where supported. No `bedrock:*`, streaming or caller-specified ARN. Ohio is a
supported source but **not an in-region Nova Micro endpoint**: the US profile
may process in `us-east-1`, `us-east-2`, or `us-west-2`. This is an explicit
synthetic-data processing-location exception for review, not a claim of Ohio
residency. If the account SCP disallows a destination, fail closed; do not
broaden it or silently switch models. [Nova Micro model card][aws-nova]

**[Lab policy]** Before invoking, commit an append-only AI-attempt audit with
tenant, effective human/ASU/client/grant, source WIDs and included field names,
prompt-template version, prompt SHA-256, model alias and request ID. No raw
prompt or answer in logs. Audit failure → 503 with **no Bedrock call**. Do not
hold a DB transaction open across network inference. Revalidate identity,
grant, and all included data permissions before returning the response; on
revocation return denial and discard it, but account for tokens already spent.
Revocation cannot retract a prompt already sent to Bedrock.

**[Lab policy]** Durable per-tenant daily usage ledger and invocation reservations
live in PostgreSQL under FORCE RLS, not EMF. Defaults: 100 attempted generations,
1,000,000 input tokens and 100,000 output tokens per UTC wall-clock day; at most
two concurrent outstanding invocations per tenant. Atomically lock the tenant
day row and reserve before dispatch; reject exhausted limits with 429
`AI_LIMIT_EXCEEDED` and Retry-After. To avoid depending on unverified Nova token
count support, conservatively reserve the model's full input-context ceiling
of 131,072 tokens and 512 output tokens per call, then settle down to returned
Converse usage. The small input byte cap is separate. This intentionally
underutilizes a nearly exhausted budget; it cannot oversubscribe it through
parallel requests. Count a failed dispatched attempt against the call limit.

**[Lab policy]** Record success/failure/unknown outcome, Bedrock request ID,
latency and actual input/output tokens in append-only invocation-result audit;
settle the mutable quota counters in the same DB transaction. A timeout or crash
after dispatch keeps the token reservation charged as unknown, never as zero.
After a 60-second lease, a subsequent request/operator repair releases only its
concurrency slot, not its charged budget. No queue/scheduler, no automatic
Converse retry that might double-spend. A failure before dispatch releases the
token reservation and attempted-call count. Expired RESERVED records also refund;
expired DISPATCHED records keep their charge. A committed dispatch marker followed
by a crash before the network call is conservatively unknown. Known provider
access/validation/throttling/not-found rejection counts the attempt with zero
tokens. Late actual usage settles an UNKNOWN record once on its original day.
If post-call audit/settlement fails, return 503 and retain the reservation.
Inference calls use a 30-second read timeout and 5-second connection timeout. Local deterministic
tests use an injected clock for ledger/lease behavior; AWS metering uses wall
time, not the frozen business clock.

**[Lab policy] Invocation logging:** application JSON logs and audit above
record every dispatched inference and outcome, without content. Bedrock's
optional full prompt/response logging stays **off**; it would duplicate
sensitive authorized data into another store. Logs go to the encrypted 3-day
group and EMF token metrics (§7.2). No Bedrock Knowledge Base, vector database,
Agents, AgentCore, model training, provisioned throughput, or Guardrails service
is required. Permission filtering is the security boundary; a prompt is not.

**[Lab policy] Deterministic test mode:** `MW_AI_BACKEND=fake|bedrock`, default
`fake` for local/pytest and explicit `bedrock` only in approved AWS deployment.
This is the sole named AI backend configuration, not an extra feature flag.
A plain fake-model function exposes exact system/prompt arguments to test spies
without persisting them; it returns fixed source-ID text (Q&A abstains) and
64/16 usage, and supports failure/timeout fixtures by
pytest monkeypatch. Tests never call AWS, including token counting. Exercise
injection documents as inert content, hidden salary sentinels absent from the
prompt, cross-tenant source rejection, and no tools/network actions even when
the fake emits malicious instructions. A live smoke sample checks model output
but cannot prove that a probabilistic model always resists prompt injection.

**[Lab policy] Enablement at checkpoint 4:** verify AWS CLI v2/current SDK and
caller identity, inspect `list-inference-profiles`/`get-inference-profile` in
Ohio, confirm the allowlisted model/destinations and IAM/SCP/quota access, then
perform one tiny synthetic Converse smoke invocation. Amazon models are not
Marketplace subscriptions and do not need Anthropic's first-use form. Current
Bedrock access is generally automatic subject to IAM/SCP; inspect account
errors rather than assuming the old “request model access” screen is required.
If a model-specific enablement/terms step is shown, an authorized operator must
complete it before declaring readiness. No account enablement or paid invocation
occurs at checkpoint 1. [Model access prerequisites][aws-bedrock-access]

**[Lab policy] Price:** Nova Micro Standard costs **$0.000035 per 1K input
tokens** and **$0.000140 per 1K output tokens** in the retrieved Ohio offer
(2026-10-08), excluding other services. For 1K input + 512 output, generation
cost is $0.00010668; 1M input + 100K output is $0.049. On-demand has no idle model
hosting charge. These are planning rates, not Workday billing or a quality
claim. [Bedrock pricing][price-bedrock], [Ohio offer][price-bedrock-ohio]

### 7.2 Embedded Metric Format (M)

**[Lab policy]** Emit raw JSON EMF lines to stdout using the same CloudWatch log
pipeline; no metrics SDK, PutMetricData, telemetry collector or tracing. Include
`_aws.Timestamp` as wall-clock epoch milliseconds, namespace `MockWorkday`,
`CloudWatchMetrics` with explicit dimension sets, root-level numeric values and
units, and `StorageResolution=60`. Request ID remains a log property, **never a
metric dimension**. [EMF specification][aws-emf]

| Metrics | Exactly one dimension set | Cardinality at five tenants |
|---|---|---:|
| `RequestCount`, `LatencyMs`, `AuthorizationDenials`, `ServerErrors`, `BedrockFailures` | `[Service, Environment]` | 5 series |
| `BedrockInputTokens`, `BedrockOutputTokens`, `AILimitDenials` | `[Service, Environment, TenantId]` | 15 series |

**[Lab policy]** Request latency is the full handler duration, including cloud
calls; emit positive latency samples and count denials/errors on the relevant
outcome. Token metrics use returned usage only; unknown charged reservations
stay in the DB and logs, not fabricated model usage. Only validated configured
tenant IDs become dimensions. Do not emit additional per-route, per-user,
per-request, client, model or ASU dimensions or automatic rollups. Twenty active
series at the first-tier $0.30/metric-month cost **$6/month**, plus log ingestion.
Each further tenant adds three series ($0.90/month). EMF does not make custom
metrics free and is not a reliable billing/limit enforcement channel.

**[Lab policy]** One private IAM-only CloudWatch dashboard contains request
count/p95 latency, denials/5xx, per-tenant token use, Bedrock failures, EventBridge
delivery/DLQ status and existing ECS/RDS health. No public sharing/Cognito. Add
three action-free standard alarms to the three DLQ alarms: p95 latency >2s,
authorization denials >20/5min, Bedrock failures >0/5min, each 2 of 3 periods
(300s), missing data `notBreaching`. These thresholds are lab starting points,
not an SLO. Inspect `EMFParsingErrors`/`EMFValidationErrors` during smoke testing.
No SNS. Budget one dashboard $3/month and six single-metric alarms $0.60/month;
all are destroyed after the session. Metrics themselves cannot be deleted on
demand and age out under CloudWatch retention; stop publishing and list those
non-active historical series as informational leftovers.

### 7.3 Contract/configuration changes to approve

**[Lab policy]** Application configuration additions are explicitly limited to
`MW_AI_BACKEND` (§7.1), `MW_PUBLIC_DOMAIN` (§5.2),
`MW_TENANT_DATA_ROLE_ARN` (§4.1.1),
`MW_TENANT_STORAGE` (tenant WID → bucket/key mapping; absent means existing local
DB bodies), `MW_CREDENTIAL_STORE` (AWS secret references or private local file),
and `MW_EVENT_BUS_ARN` (absent means local test capture, never AWS publication).
Use the AWS credential chain/task role, not static access-key settings. Region
comes from standard AWS environment. Fixed lifetimes/limits above are not flags.
No generic feature flags. The named AI backend/storage settings select concrete
local and AWS implementations. Cloud clients are synchronous; add boto3 only
for these specified AWS calls. No other application settings are introduced.

**[Lab policy]** The owner creates `ai_usage_daily` (tenant/day counters and
reservations) and `ai_invocations` (tenant, invocation/request/actor IDs, dispatch
state, reserved/actual usage, lease deadline), with app writes limited to those
metering fields. Attempt/result audit is a separate append-only record. All use
tenant predicates/FORCE RLS; no raw prompt/answer is persisted. Export records
hold owner/client/grant, object reference, source/filter metadata and expiry,
never the presigned URL. Neither table represents customer agent activity.

**[Lab policy]** Add only small concern modules for credential storage, object
storage, report export, native AI/metering, and business-event publication if needed. Existing auth,
authz, BP, and audit functions remain the decision points. The approved OpenAPI
must include new token forms, report shapes, limits, errors and admin contracts;
event detail JSON Schema v1 is a separate published artifact alongside it.

**[Lab policy]** Required main-spec amendments at checkpoint 2:

| Existing section | Approved delta to fold in |
|---|---|
| §1–2 | ASU account kind, tenant identity tables/RLS, secret references, document object metadata, export records, BP history transition fields |
| §3–4 | New token forms, compatibility rules, current credential validation, fixed operation ceilings |
| §5–6 | Audit actor vs effective human, transactional outbox publication semantics, report endpoint; preserve BP receipts/locking |
| §7–8 | S3 bodies, report capability exception, actor attribution and structured logs |
| §9–11 | Admin lifecycle, deterministic bulk generator and balances, native AI fake/usage ledger, M3 test IDs |
| §12–13/D1 | ABAC sessions, EMF, WAF, private/optional public TLS, Bedrock processing regions, costs and integration boundary exceptions |

## 8. Cost and teardown (E; all infrastructure is lab policy)

### 8.1 Price basis and baseline

**[Lab policy]** USD on-demand list prices retrieved 2026-10-08, `us-east-2`;
no credits/free-tier subtraction. Monthly equivalents use **730 hours**, not a
promise to keep anything up. Public AWS offer files verified the regional
compute/storage/LB/endpoint/secret/KMS/SQS rates; links below permit rechecking.
Refresh at checkpoint 3. Assumptions: five tenants, one agent with two ASUs per
tenant, one consumer target/DLQ, one consumer endpoint AZ, 1 GB total S3 data,
one ECS task, two ALB public addresses plus one task address.

| Item | Hourly or hourly equivalent | 730-hour month | Source |
|---|---:|---:|---|
| D1 Fargate ARM, 0.25 vCPU / 0.5 GiB | $0.009875 | $7.21 | [ECS Ohio rates][price-ecs] |
| D1 RDS PostgreSQL `db.t4g.micro` | $0.016000 | $11.68 | [RDS Ohio rates][price-rds] |
| D1 RDS gp3, 20 GB at $0.115/GB-month | $0.003151 | $2.30 | [RDS Ohio rates][price-rds] |
| D1 public ALB base | $0.022500 | $16.43 | [ELB pricing][price-elb], [Ohio rates][price-elb-ohio] |
| D1 three public IPv4 addresses | $0.015000 | $10.95 | [VPC Ohio rates][price-vpc] |
| Internal NLB base (provider) | $0.022500 | $16.43 | [ELB pricing][price-elb] |
| PrivateLink interface endpoint, one AZ (consumer-owned) | $0.010000 | $7.30 | [PrivateLink pricing][price-pl], [Ohio rates][price-vpc] |
| Five tenant KMS keys | $0.006849 | $5.00 | [KMS pricing][price-kms] |
| One operational KMS key (logs, TLS secret, DLQs) | $0.001370 | $1.00 | [KMS pricing][price-kms] |
| Ten ASU credential secrets | $0.005479 | $4.00 | [Secrets Manager pricing][price-secrets] |
| One TLS leaf secret | $0.000548 | $0.40 | [Secrets Manager pricing][price-secrets] |
| Three existing DB secrets | $0.001644 | $1.20 | [Secrets Manager pricing][price-secrets] |
| One private hosted zone | $0.000685 equivalent | $0.50 | [Route 53 pricing][price-dns] |
| S3 Standard, 1 GB total | $0.000032 equivalent | $0.023 | [S3 pricing][price-s3], [Ohio rates][price-s3-ohio] |
| Six standard CloudWatch metric alarms, one target | $0.000822 equivalent | $0.60 | [CloudWatch Ohio rates][price-cw] |
| Event bus/rule, empty DLQ, lab CA, imported ACM certificate | $0 fixed | $0 fixed | Usage below; [ACM pricing][price-acm] |
| Twenty EMF custom metric series | $0.008219 equivalent | $6.00 | [CloudWatch pricing][price-cloudwatch] |
| One CloudWatch dashboard | $0.004110 equivalent | $3.00 | [CloudWatch pricing][price-cloudwatch], [AWS dashboard cost example][price-dashboard] |
| WAF web ACL + two managed-group references + one rate rule | $0.010959 equivalent | $8.00 | [WAF pricing][price-waf] |
| **Baseline including consumer endpoint, excluding usage** | **$0.139742** | **$102.01** | Sum of unrounded items |

**[Lab policy]** Per-item marginal cost: another tenant adds a $1/month KMS key
and S3 usage; another ASU adds $0.40/month secret storage; another endpoint AZ
adds $0.01/hour; each tenant also adds three metrics ($0.90/month); another target adds three alarms ($0.30/month) and request/DLQ
usage. Runtime-owned signing secrets/keys, consumer bus/queue processing, and
runtime compute are excluded except the explicitly shown interface endpoint.
Secrets Manager rotation versions do not imply another secret container.

| Usage item | Unit price; monthly cost formula (hourly equivalent = result / 730) |
|---|---|
| ALB / NLB capacity | $0.008/LCU-hour and $0.006/NLCU-hour; integrate measured units over time |
| PrivateLink processing | $0.01/GB first tier; add regional/cross-AZ transfer as applicable |
| S3 writes/lists and reads | $0.005/1,000 PUT/COPY/POST/LIST; $0.0004/1,000 GET; export downloads incur GET and applicable transfer charges |
| KMS symmetric requests | $0.03/10,000; count actual requests after Bucket Key savings; do not assume one decrypt per GET |
| Secrets Manager API | $0.05/10,000 calls; verification calls are usage, not free |
| EventBridge | $1/million custom events ingested, plus $1/million custom events delivered to another bus; billed in 64-KB units ([pricing][price-events]) |
| SQS standard DLQ | $0 fixed/hour; $0.40/million first-tier requests before free tier, including receives/deletes/empty polls; KMS extra ([SQS pricing][price-sqs], [Ohio rates][price-sqs-ohio]) |
| CloudWatch Logs | $0.50/GB standard ingestion and $0.03/GB-month archive storage at first tier; 3-day retention bounds storage, not ingestion ([Ohio rates][price-cw]) |

**[Lab policy]** Optional public DNS adds $0.50/month per hosted zone (about
$0.000685/hour equivalent) plus non-alias query charges; ACM non-exportable
public TLS for the ALB adds no certificate charge. Registrar fees are external.
With that zone, the baseline is $102.51/month equivalent, $0.140427/hour.
Bedrock adds no fixed hourly/monthly hosting charge; per-token costs are in §7.1.
WAF requests add $0.60/million at the selected baseline capacity.

**[Lab policy]** Bucket Keys control KMS request cost, not key storage cost.
No NAT gateway, Private CA, paid DNS Resolver endpoint, EventBridge archive,
dedicated CloudTrail trail, or S3 interface endpoint is budgeted. Tasks use
existing D1 egress to AWS HTTPS APIs. Requests, data transfer, RDS burst CPU
credits, image storage, and temporary bootstrap-task time can add cost; this is
a baseline, not a spending cap. D1's older $0.07–0.08/hour estimate is not the
new all-in figure.

**[Lab policy] Billing caveats:** Route 53 charges $0.50 when a zone is created
and each following month, without partial-month proration; AWS exempts a zone
deleted within 12 hours. Private-zone DNS queries have no additional charge.
Endpoint partial hours are billed as full hours. KMS key storage is prorated;
scheduled-deletion keys incur no key-storage charge unless deletion is canceled.
CMK material rotation can add retained-version charges; ASU credential rotation
is separate. Keep these lifecycle effects in a short-session cost report.

### 8.2 Tenant deletion and disposable teardown

**[Lab policy]** Tenant deletion first disables tenant HTTP/token access and
grants, removes its event subscription, deletes document/export objects and ASU
secrets, and removes tenant DB records with owner authorization. Disable and
schedule deletion of that tenant's key: **crypto-shredding** ultimately removes
the ability to decrypt remaining ciphertext protected by it. Disabling is
reversible; scheduling is cancelable; only completed key deletion is final.
It cannot erase downloaded plaintext, previously disclosed exports, DB rows
under RDS's separate key, or old audit/log metadata. Bucket Key/service caches
mean key disabling alone is not an immediate per-request revocation mechanism;
use API denial and S3 denial/object deletion as well.

**[Lab policy]** AWS requires a **7–30 day** KMS deletion waiting period; use
7 days for disposable lab keys and record actual `DeletionDate`. The service
must never call this on a platform/state key. [KMS deletion][aws-kms-delete]
No tenant-deletion public API is added now; this is the operator deletion story
and must be tested as an isolated synthetic-tenant scenario at checkpoint 4.

**[Lab policy]** Extend `aws-down` in dependency order:

1. Stop new API work/event publication; record session results and DLQ counts.
2. Runtime owner deletes its interface endpoint and disconnects its DNS/VPC
   association and event target. Mock Workday removes its own forwarding rules,
   targets, DLQs, bus and alarms. Never delete the receiver's queue or bus.
3. Remove private DNS records/zone associations, endpoint service, NLB and target
   group; remove imported ACM certificate after listener detach. Detach/delete
   the WAF ACL, dashboard and optional public DNS/certificate resources. Preserve D1's
   operator path until any required admin cleanup is complete.
4. Stop tasks; empty/delete tenant buckets (including any accidental versions,
   delete markers and incomplete multipart uploads), delete ASU/TLS secrets
   without recovery delay, and remove D1 service resources/log groups.
5. Remove tenant-data role/policies, service-owned aliases and schedule tenant/operational KMS keys for
   7-day deletion after encrypted resource cleanup. Remove local lab CA/leaf
   private files. A fresh deployment gets fresh keys and deterministic seed IDs.

**[Lab policy]** `aws-leftovers` uses service inventories, preserving D1's stale
tag handling. Add S3 buckets/objects/versions/uploads, KMS keys/aliases/deletion
dates, all ASU and TLS secrets, imported ACM certs, private hosted zones and
associations, endpoint services/connections, interface endpoint IDs, NLBs,
EventBridge buses/rules/targets, SQS DLQs and alarms, the EMF dashboard, WAF ACL/association, optional public zone/records,
ACM certificates, and tenant-data role/policies. Keep D1 RDS/snapshot/task/
IP/ENI/log checks and both log Regions. API errors fail the check.

**[Lab policy]** A clean verdict means no active/billable service resources,
plus an explicit inventory of expected nonbillable KMS keys in `PendingDeletion`
with dates. Do not print “nothing remains” while keys still exist. Recheck after
their dates. Enabled or merely Disabled unscheduled CMKs fail; so do remaining
buckets, secrets, zones, targets, queues, or consumer endpoints for this service.
If consumer inventory cannot be read, require its owner's evidence and report
integration cleanup **unverified**, not clean. Persistent platform state,
foundation and D1 ECR registry are the existing named exemptions; ECR/state
storage costs are not zero. Never destroy them to obtain a clean verdict.

## 9. Proposed acceptance matrix

**[Lab policy]** Append these stable IDs to spec §11 after approval; use them
in pytest names. Preserve every M1/M2/D1 test. AWS assertions below run only
at checkpoint 4; checkpoint 3 is fmt/validate/plan only.

| ID | Required coverage |
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
| T-M3-S-01 | API-only documents, unchanged metadata/content shapes/limits; derived tenant keys; cross-tenant denial before S3 |
| T-M3-S-02 | Upload/read/audit failures, integrity mismatch, rollback orphan cleanup; local DB mode and AWS stub behavior |
| T-M3-S-03 | Live bucket/key policy, Bucket Keys, wrong role/key rejection, CloudTrail KMS evidence, deletion/cache limitation |
| T-M3-SEED-01 | Deterministic new workers/compensation/docs, original visibility/vacancies preserved, pagination, inert injection controls |
| T-M3-R-01 | Report current row/field authorization, scope/operation checks, snapshot, size limit, no document-body export |
| T-M3-R-02 | Export audit failure/S3 failure gives no URL; TTL caps and wall time; URL reuse, expiry and grant-revocation limitation; lifecycle cleanup |
| T-M3-N-01 | Private exact tenant DNS, CA trust/hostname checks, unknown host/cross-tenant token, rejected unapproved endpoint |
| T-M3-N-02 | D1 operator smoke unchanged; admin/DB unreachable via both LBs; no runtime S3/secret access except report bearer URL |
| T-M3-E-01 | Commit-only types/schema/tenant/actor/version; final vs intermediate approval; no sensitive content or authority |
| T-M3-E-02 | Per-entry PutEvents failures, retry/timeout duplicate ID, atomic outbox/history rollback, restart recovery, crash after acceptance before mark, backoff and bounded repair; rollback emits nothing |
| T-M3-E-03 | Cross-domain tenant filtering, no credentials in events; duplicates/out-of-order fixtures and authorized HTTP refetch |
| T-M3-E-04 | One DLQ per target, policy/KMS correctness; permanent target failure retained, alarms visible, controlled redrive preserves detail ID |
| T-M3-ABAC-01 | Missing/wrong tag, prefix, bucket, secret and key rejected; TagSession trust; correct tenant session succeeds; base task role cannot bypass |
| T-M3-ABAC-02 | Per-tenant concurrent session caching/refresh, disabled-tenant checks, no credential leakage, presign capped by session expiry |
| T-M3-SEED-02 | Bulk manifest/hash/count reproducibility, occupancy/history/BP consistency, retry after partial RDS/S3 load, no publish/inference during seed |
| T-M3-BAL-01 | Balance snapshot as-of/current ABSENCE permission and cross-tenant isolation; live time off does not silently mutate snapshots |
| T-M3-AI-01 | AI_USE/scopes/operation ceilings; hidden workers, documents and salary absent from fake-model input; cross-tenant and oversized requests rejected before dispatch |
| T-M3-AI-02 | Injection/control documents exercised; model/URL/tool overrides rejected; no actions performed from output; no-source/no-answer behavior |
| T-M3-AI-03 | Concurrent daily quota reservations, input/output settlement, 429, midnight/lease expiry, timeout/crash budget retained, audit failure before/after dispatch |
| T-M3-AI-04 | Mid-call identity/grant/object permission revocation discards answer; spent tokens remain charged; logs contain no prompt/answer/credentials |
| T-M3-AI-05 | All local tests use deterministic fake without AWS; approved live smoke verifies exact profile/IAM/regions/usage and access failure without fallback |
| T-M3-M-01 | Valid EMF JSON/units/wall time, exactly specified dimensions/20 baseline series; dashboard/alarms plan, no SDK/collector, historical metric leftover distinction |
| T-M3-WAF-01 | Public ALB association only, managed/rate block, legitimate 64-KiB document and injection fixture behavior, private path unaffected, app authorization still required |
| T-M3-DNS-01 | Null-domain D1 path; configured public aliases, NS/ACM validation, HTTPS redirect and host isolation; canonical token identity unchanged; TLS teardown |
| T-M3-DOWN-01 | Resource ownership/SSM-only plan, no forbidden resources; clean teardown, pending KMS inventory, failed/unreadable inventories non-clean |

## 10. Decision register: choices, alternatives, and reasoning

**[Lab policy]** Every row is an approved lab decision unless it explicitly says
Workday-inspired. It is not evidence of Workday's internal design. Unknown
implementation details are handled by these stated assumptions, not a research
blocker. The implemented contract changes slice by slice at checkpoint 2.

| Decision | Alternative considered | Why (reasoning to defend) |
|---|---|---|
| D01 — A: Tenant registration with at most one ASU per mode; Workday-inspired | One global identity or one account shared across modes | Tenant boundaries and separate autonomous/delegated authority stay explicit; matches the published identity distinction without modeling a runtime |
| D02 — A: Reuse grants as the sole consent record | Add a second agent-consent/refresh database | One revocation source avoids inconsistent authority and preserves the existing integration |
| D03 — A: Delegate ASU client + active credential + grant-based renewal, no fresh human token | Require human presence on every exchange | Workday documents consent with access and refresh tokens; the grant is our refresh-token analogue, one revocation record, with current human authority checked per call; legacy clients stay compatible |
| D04 — A: JWT-bearer ambient auth with pinned registered X.509 key | Shared ambient secret or accepting arbitrary certificate subjects | Proof of key possession maps to a server-owned tenant/client binding; key text never selects authority |
| D05 — A: Fixed scopes plus a fixed operation ceiling | Full Workday skill catalogue/policy DSL; scopes alone | Prevents coarse-scope privilege amplification with a short direct check |
| D06 — A: Revalidate authority/credential status per call; 300s access tokens, 60s assertions | Rely on token expiry or long authorization caches | Prompt revocation and understandable bounded credential exposure; in-flight actions remain a documented limit |
| D07 — A/H: Two accepted versions with 300s overlap and immediate emergency revocation | Cut over instantly or accept old keys indefinitely | Allows deployment handover without unlimited compromised-key validity |
| D08 — H: Secrets Manager/KMS verification store; external signer owns private key | Shared cross-domain secret access or Mock Workday custody of every external private key | Each domain owns its credentials; the verifier needs only a public key/hash; avoids inventing a first-party custody service |
| D09 — A: Separate actor ASU from effective human in audit | Replace human principal fields with ASU | Preserves BP/self-approval rules while making who acted and whose authority unambiguous |
| D10 — A: Isolated test-admin registration for M3 | New public tenant-admin IAM system | Existing lab admin boundary is sufficient; production administration deserves an explicit later security model |
| D11 — B: Defer A2A/MCP gateway | Duplicate all REST operations over two new protocols now | No agreed protocol-specific core interaction justifies the additional authorization and compatibility surface |
| D12 — C: One bucket and one tenant CMK, Bucket Keys enabled | Shared bucket/key; per-object CMKs | Five tenants make clear policy boundaries cheap; Bucket Keys reduce request cost while retaining tenant key deletion |
| D13 — C/L: Tenant-tagged STS role with prefix/key restrictions and 900s cached sessions | Broad app S3/KMS permissions; IAM role per tenant | Adds a cloud-side check below DB isolation without a role explosion or per-request STS call; trusted app remains the tag issuer |
| D14 — C: S3 immutable bodies, DB metadata/authorization; write object before DB commit | Store everything in S3; distributed transaction | Ordinary relational security stays intact; bounded orphan cleanup is simpler than pretending atomic cross-service commits; scan at most 1,000 keys per owner call, with a 24-hour age floor to avoid fresh in-flight uploads |
| D15 — C/E: Crypto-shredding plus explicit row/object deletion | Promise immediate total erasure on key disable | Distinguishes key caches, reversible disabling, delayed final deletion, RDS data and already-disclosed copies honestly |
| D16 — K: Three bulk tenants with fixed-seed/versioned generator; original fixtures unchanged | Enlarge Acme/Globex and rewrite the regression matrix | Realistic scale without destabilizing carefully defined access examples; manifest hashes detect generator drift; one shared synthetic password hash per bulk tenant avoids thousands of KDF calls |
| D17 — K: Consistent historical records and read-only balance snapshots | Full accrual/payroll/absence accounting engine | Supplies useful context while keeping the existing two-process learning scope and no-live-balance rule explicit |
| D18 — K: Resumable bounded bootstrap import, no seed-time notifications | Regenerate live data on each app start; unbounded parallel import | Protects modified test data and keeps small Fargate memory/RDS capacity usable |
| D19 — I: Bounded synchronous NDJSON report | Async job/queue, arbitrary reporting DSL, all rows in memory | Fits the simple stack and thousands-of-workers lab while making report authorization inspectable |
| D20 — I: 60s presigned report capability, API-only ordinary documents | Proxy every export; long-lived download URLs | Exercises common SaaS export behavior and reduces API transfer work; bearer/revocation tradeoff is explicit |
| D21 — D: Provider PrivateLink/NLB; consumer endpoint owned by runtime | Public-only integration, broad peering routes, a new shared VPC | Exposes one private service without coupling databases or network routing domains |
| D22 — F: Exact private tenant DNS under mockworkday.internal and offline lab CA; TLS ends at NLB | Private CA monthly fee; disabling TLS verification; backend TLS now | Tests trust and hostname identity cheaply; internal HTTP hop remains an explicit lab simplification |
| D23 — G: Versioned minimal EventBridge notifications with stable transition ID | Full business payload or events as commands | Limits sensitive copies and makes duplicates/order/reconciliation manageable; authority stays in HTTP |
| D24 — G: Transactional outbox and small dispatcher in the same image | Synchronous post-commit publish and operator-only recovery | Atomic BP history/outbox insertion closes the dual-write loss gap; stable IDs make crash retries deduplicable; no internal work queue or new deployment |
| D25 — G: One provider standard SQS DLQ per cross-domain bus target | Drop failed events; provider owns consumer queue | Retains diagnosable target failures while preserving team/domain ownership; no SNS needed |
| D26 — J: Workday-inspired native fixed AI functions and bounded text API | Host customer code or create an agent/tool runtime | Adds platform AI capability within core-service scope, not a second agent runtime |
| D27 — J: Filter current object/field access before inference; recheck before response | Prompt the model with all tenant data and ask it to redact | Authorization is deterministic code, not model compliance; explains exactly when data leaves the service |
| D28 — J: Explicit permitted document IDs and small context | Vector store/managed knowledge base or unbounded search | Existing permission checks work on concrete sources; no extra index copies or stale retrieval ACLs |
| D29 — J: Nova Micro through a single US profile | Larger premium model, arbitrary model proxy, silent fallback | Cheap text-only model fits summaries; one allowlist makes spend/IAM review simple; US processing exception is explicit |
| D30 — J: Own IAM role, non-content invocation audit | Caller cloud keys; full Bedrock prompt logging | Credentials never cross the API; correlation/usage are observable without copying salaries or documents into logs |
| D31 — J: DB quota reservations, conservative input ceiling, no automatic retry | Token estimates from characters; count only successful replies | Concurrent calls and unknown outcomes cannot evade limits; lower utilization near the cap is acceptable in a lab |
| D32 — J: Fake model by default, no tool execution or prompt cache | Paid live model calls in pytest; shared cached answers | Determinism and no accidental charges; avoids cache-key/authority leakage and does not overclaim injection immunity |
| D33 — M: Raw EMF, five service metrics and three per tenant | Telemetry SDK/traces or dimensions per request/user | Small inspectable telemetry with bounded cardinality and explicit metric charges; DB remains metering authority |
| D34 — M: One IAM-only dashboard and six action-free alarms | Per-tenant dashboards, SNS paging or public sharing | Enough session visibility without operating a notification system or adding Cognito |
| D35 — N: Two AWS baseline groups + rate rule on public ALB | No WAF; Bot/Fraud Control/CloudFront; WAF as authorization | Adds basic public-edge filtering at known cost; preserves /32 and application tenant checks, allows legitimate document sizes |
| D36 — O: Optional externally owned public domain, explicit aliases, ACM wildcard | Buy via blocked Route 53 Domains; require a domain for all dev work | HTTPS works when a domain is available; null default preserves reproducible D1 testing |
| D37 — O: Public/private host aliases share canonical issuer/audience | Change token issuer by transport host | Keeps identity and grants stable across private and operator paths; exact alias matching prevents tenant confusion |
| D38 — E: Disposable stack, 7-day pending key inventory, persistent platform exceptions | Keep resources overnight; delete shared foundation for a clean check | Meets cost/ownership constraints while reporting unavoidable KMS and metric-history leftovers truthfully |
| D39 — Scope: No CloudFront/X-Ray/Macie/CloudHSM, multi-AZ RDS, autoscaling, SNS or internal queues beyond target DLQs | Add production infrastructure wholesale | Each excluded service either lacks a current need, violates the plan/stack constraints, or is unavailable; production resemblance does not justify unused machinery |
| D40 — Integration: Versioned HTTP plus explicit event/export exceptions, no shared DB/code | Import runtime internals or share persistence | Keeps independent ownership and testable contracts despite broader enterprise-service functionality |
| D41 — K: 200-by-200 synthetic name pools, family/level architecture and industry-specific prose before first bulk load | Numeric name suffixes, unrelated role rotation and repeated filler | Makes core-service examples believable without new schema fields, real personal data or a compensation engine; preserve counts/IDs and recompute content hashes |
| D42 — H: Owner CLI prepares immutable credential versions; isolated admin API activates references | Grant Secrets Manager writes to the service | Preserves the specified read-only tenant verification role and keeps enrollment material out of Terraform/state; local test-admin can generate synthetic credentials directly |
| D43 — I: Repeatable-read synchronous NDJSON, private temporary file, bounded single S3 PutObject; local bounded DB bytes and process-signed capability | Multipart transfers, background report jobs or local filesystem object service | A 16-MiB cap fits one request and avoids multipart cleanup or another service; existing authorization applies to a coherent snapshot and local restart safely invalidates URLs |
| D44 — G: BP history UUID is transition identity; one in-process dispatcher, wall-clock retry bookkeeping and bounded owner repair | New notification identity/queue/dispatcher deployment or publish-before-commit | Durable history gives exact reconstruction and deduplication; the approved outbox closes dual-write loss while keeping deployment small and failures visible |
| D45 — G: Hold claimed rows locked through bounded PutEvents; keep looping after unexpected exceptions | Lease-then-publish with a separate acknowledgment transaction | Keeps claim/mark atomic and inspectable at lab scale; leases shorten locks at larger scale but add recovery state; class-only error logs preserve privacy while intent remains retryable |
| D46 — J: Tenant advisory lock before day-row lock; short committed reservation and dispatch marker; inference outside transactions | Hold DB locks through inference; in-memory counters; lease only by day | Serializes a tiny ledger update, enforces concurrency across midnight and keeps model latency off DB locks; an ambiguous dispatch stays conservatively charged |
| D47 — J: Refund never-dispatched expired reservations; retain uncertain dispatched charges, settle late actual usage once | Refund every expired lease; permanently charge successful calls their full reservation | Prevents timeout overspend while recovering concurrency and known unused budget; ties settlement to the original UTC day |
| D48 — J: All explicit document authorization precedes body fetch; server-only source references and post-call reauthorization | Best-effort partial document answers; trust model citations; authorize only at token issuance | Keeps authorization inspectable, avoids source enumeration and discards answers after revocation without pretending to retract sent prompts |
| D49 — M: Fixed dev environment and specified dimensions; fake uses the same local metric shape | Caller-selected dimensions; tenant dimensions on every request metric | Matches the single disposable lab deployment, bounds custom-metric cardinality, and tests EMF without creating paid metrics |

**[Lab policy]** Slice 2d implementation details and response shapes are now in
[contract §6.8](spec.md#68-native-ai-and-metering-workday-inspired-features-lab-policy-implementation);
no model-access enablement, inference, metrics resources or infrastructure deployment
was performed at this checkpoint.

## 11. Reviewer decisions and remaining deployment inputs

**[Lab policy]** Q1–Q11 are resolved: grant renewal (D03), external credential
custody (D08), transactional outbox (D24), HTTP event/export exceptions (D40),
private path (D21/D22), deferred gateway (D11), bulk scale/balances/reports
(D16–D20), cost/deletion (D15/D38), Nova Micro US processing (D29), null public
domain (D36), and operational scope (D13/D33–D35) are approved. The four
checkpoint-2 slices in §0 replace the former single implementation checkpoint.

**[Lab policy]** Private transport names and certificate SANs use
`{slug}.mockworkday.internal`, avoiding `.local` mDNS conflicts; ICANN reserved
`.INTERNAL` for private use ([ICANN resolution][icann-internal]). Canonical
issuer/audience strings remain exactly `.mockworkday.local`, including JWT
assertion audiences. Private and optional public names are explicit aliases
for tenant routing, never alternate identity issuers.

**[Lab policy]** Remaining checkpoint-3/4 inputs: runtime allowed principal,
consumer endpoint/DNS target, VPC association, destination event bus ARN and
tenant subscriptions; runtime clients must install the lab CA trust anchor.
No public domain has been supplied; its variable remains null. No further
checkpoint-1 decision blocks slice 2a. Later slices and infrastructure retain
their separate commit/review stops.

[icann-internal]: https://www.icann.org/en/board-activities-and-meetings/materials/approved-resolutions-special-meeting-of-the-icann-board-29-07-2024-en
[wd-security]: https://doc.workday.com/admin-guide/en-us/workday-ai/agents/agent-security/concept--agent-security.html
[wd-external-requested]: https://doc.workday.com/admin-guide/en-us/workday-ai/agents/agent-system-of-record/external-agents/configure-external-agents.html
[wd-external-current]: https://doc.workday.com/admin-guide/en-us/workday-ai/agents/external-agents/configure-external-agents.html?toc=0.8.1
[wd-external-stage]: https://stage.doc.workday.com/admin-guide/en-us/workday-ai/agents/agent-system-of-record/external-agents/configure-external-agents.html
[wd-external-asu]: https://doc.workday.com/admin-guide/en-us/workday-ai/agents/external-agents/concept--external-agent-asu-considerations.html
[wd-gateway]: https://newsroom.workday.com/2025-06-03-Workday-Announces-New-AI-Agent-Partner-Network-and-Agent-Gateway-to-Power-the-Next-Generation-of-Human-and-Digital-Workforces
[rfc7523]: https://www.rfc-editor.org/rfc/rfc7523
[rfc8693]: https://www.rfc-editor.org/rfc/rfc8693
[aws-s3-security]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html
[aws-s3-kms]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingKMSEncryption.html
[aws-bucket-keys]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/bucket-key.html
[aws-kms-trail]: https://docs.aws.amazon.com/kms/latest/developerguide/logging-using-cloudtrail.html
[aws-kms-delete]: https://docs.aws.amazon.com/kms/latest/developerguide/deleting-keys.html
[aws-presign]: https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html
[aws-privatelink]: https://docs.aws.amazon.com/vpc/latest/privatelink/create-endpoint-service.html
[aws-private-dns]: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/hosted-zones-private.html
[aws-cross-account-events]: https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-cross-account.html
[aws-event-retries]: https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-rule-retry-policy.html
[aws-event-dlq]: https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-rule-dlq.html
[price-ecs]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonECS/current/us-east-2/index.json
[price-rds]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/us-east-2/index.json
[price-elb]: https://aws.amazon.com/elasticloadbalancing/pricing/
[price-elb-ohio]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSELB/current/us-east-2/index.json
[price-vpc]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonVPC/current/us-east-2/index.json
[price-pl]: https://aws.amazon.com/privatelink/pricing/
[price-kms]: https://aws.amazon.com/kms/pricing/
[price-secrets]: https://aws.amazon.com/secrets-manager/pricing/
[price-dns]: https://aws.amazon.com/route53/pricing/
[price-s3]: https://aws.amazon.com/s3/pricing/
[price-s3-ohio]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/us-east-2/index.json
[price-sqs]: https://aws.amazon.com/sqs/pricing/
[price-sqs-ohio]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSQueueService/current/us-east-2/index.json
[price-events]: https://aws.amazon.com/eventbridge/pricing/
[price-cw]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/us-east-2/index.json
[price-acm]: https://aws.amazon.com/certificate-manager/pricing/
[wd-ai-tools]: https://newsroom.workday.com/2025-06-03-Workday-Unveils-AI-Developer-Toolset,-Empowering-Developers-to-Customize-and-Connect-AI-Apps-and-Agents-on-the-Workday-Platform
[wd-illuminate]: https://www.workday.com/content/dam/web/en-us/documents/solution-brief/workday-illuminate.pdf
[aws-session-tags]: https://docs.aws.amazon.com/IAM/latest/UserGuide/id_session-tags.html
[aws-emf]: https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html
[aws-waf-groups]: https://docs.aws.amazon.com/waf/latest/developerguide/aws-managed-rule-groups-baseline.html
[aws-acm-dns]: https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html
[aws-nova]: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-micro.html
[aws-bedrock-access]: https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html
[price-bedrock]: https://aws.amazon.com/bedrock/pricing/
[price-bedrock-ohio]: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonBedrock/current/us-east-2/index.json
[price-cloudwatch]: https://aws.amazon.com/cloudwatch/pricing/
[price-waf]: https://aws.amazon.com/waf/pricing/

[aws-waf-body]: https://docs.aws.amazon.com/waf/latest/developerguide/web-acl-setting-body-inspection-limit.html
[price-dashboard]: https://docs.aws.amazon.com/solutions/latest/automated-security-response-on-aws/cost.html
