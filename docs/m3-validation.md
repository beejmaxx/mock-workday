# MW-M3 checkpoint 4 — live provider validation

**Lab policy and observed lab results, 2026-10-08 (Asia/Shanghai).** This is
Mock Workday evidence, not a claim about Workday infrastructure. Deployment used
approved commit `5fa0502` in account `729608197929`, Ohio, with profile
`agent-runtime` and verified caller `arn:aws:iam::729608197929:user/lab-operator-cli`.
**Checkpoint 4 is accepted.** The reviewer accepted the live evidence in
`d8e886d` and approved owner-scoped cleanup (M3 decision D53). Mock Workday has
no active service leftovers; S1 resources and permanent platform cost-guard
infrastructure belong to other owners and remain informational with their tags.
The six scheduled KMS deletions below remain explicit.

Public domain and all consumer inputs remained unset. No platform resources were
changed, and `/admin/reset` was never called.

## Deployment

`make aws-up` detected and saved direct egress `120.229.48.82/32`. The default
Docker socket was unavailable on the first attempt; retrying with the already
running `DOCKER_CONTEXT=colima-s1-builder` completed the ARM64 build. The registry
apply had no changes. Image tag `5fa0502f6288` has digest
`sha256:6885f54866e212bbf0535392584b06e470e2edf90d8d8aaaf4da05c3b9be2bfa`.

Before answering the service apply prompt, resource-by-resource text comparison
against the saved provider plan found **115 add, 0 change, 0 destroy**: the
reviewed 114 resources plus the private TLS listener, and the expected second
load-balancer attachment on the new ECS service. Apply completed with those
counts. The imported leaf was then stored successfully in the operational secret.
CA and leaf files were mode `0600` under ignored `.local/m3-tls`; no private key
entered Terraform or git.

Migration task `f64a98c019744d39b32d9800fc73662d` bootstrapped RDS, loaded all three
bulk tenants and seeded the disabled ASUs. The wrapper reported migration success
and a stable service. ECS reports exit code 0, running from
`2026-10-07T21:10:34.118Z` to `21:21:23.735Z` (about 10m50s).
`make aws-smoke` passed: login/read 200, compensation 403,
cross-tenant 404, and revoked delegation grant 401.

## Data and API evidence

Owner SQL checks matched each `bulk-v1` manifest and every generated table count;
separately seeded ASUs are excluded from the manifest's human-account count.
Each bulk tenant has two disabled ASUs. S3 object counts and byte totals match
manifest document totals; bulk document bodies are absent from RDS. All three
buckets report SSE-KMS with their respective tenant key and Bucket Keys enabled.

| Tenant | Workers | Organizations | Documents / S3 objects | Document bytes | Manifest SHA-256 |
|---|---:|---:|---:|---:|---|
| northstar | 512 | 43 | 2,048 | 20,950,016 | `16f46ec8e91cc9e866c94be9b268a504440736ac604f9db6317ea1651ea4623e` |
| meridian | 1,024 | 85 | 4,096 | 41,936,896 | `7d355c42e41da0651b2a34ed63639dbd4cbb9b5040c55a05664e85dee229cbd7` |
| cedar | 2,048 | 171 | 8,192 | 83,874,816 | `4edb529aab69ded89fa36c9bd38490cd6e5bd286add76d8de7ac909adb5a28c1` |

Measured RDS database size after imports and API probes: **45,094,579 bytes**.
Bulk document content totals **146,761,728 bytes**. These are measured data sizes,
not allocated RDS storage or billed S3 usage. Original small fixtures remained
available for smoke tests; Globex was subsequently deleted in the isolated test.

- Report creation returned 201 and the presigned S3 download returned 200. Bob's
  roster contained one authorized row with only his compensation. A separate URL
  had `X-Amz-Expires=60`, returned 200 initially, and 403 after expiry. URLs and
  tokens are not reproduced here.
- Time-off event `b851edf5d56e4f94a7bf2b2beb00f984` was submitted and approved.
  Outbox event `950467b3-44db-4666-a239-eb44e0d62fe4` had `attempt_count=1`,
  `last_error=null`, and `published_at=2026-10-07T21:22:35.424316Z`. This proves
  publication acknowledgement by the provider bus, not delivery to a consumer.
- The public admin-path probe returned 404; ECS Exec reached the isolated admin
  OpenAPI on `localhost:8081` with 200. No reset was performed.

## IAM, encryption, AI and observability

Probes ran through ECS Exec as service task
`e7028f8c64d14589943c83604ef68ed2`, using task credentials and a correctly tagged
STS session for the positive control. No temporary credentials were printed.

| Probe | Observed result | AWS request ID |
|---|---|---|
| Missing tenant tag | AccessDenied | `df7ccb0d-b20f-4c21-b2b9-e6fffa81d92d` |
| Unknown tenant tag | AccessDenied | `786043c9-2614-4808-b188-c85ad774ac59` |
| Additional unapproved tag | AccessDenied | `796e1174-aa09-436f-8ebe-b10e4dfa63ae` |
| Correct tagged S3 put/read | Success; `aws:kms`, Bucket Key true | `5XPA2CAWGD9X6GF4` |
| Base task S3 read | AccessDenied | `5XP91V1CMJQY5YGD` |
| Cross-tenant bucket | AccessDenied | `5XPCYAZTY1TZ02D8` |
| Wrong object prefix | AccessDenied | `5XP29Y99XZFANW49` |
| Wrong key on S3 write | AccessDenied | `5XPA48G1W2C1CMAR` |
| Cross-tenant KMS | AccessDeniedException | `75fb90a0-8de7-4704-bcc2-c46f8b8ef1aa` |
| Cross-tenant secret | AccessDeniedException | `f3b7291b-85d5-4b02-af96-2a8cf658656b` |
| Base task secret read | AccessDeniedException | `834fdecb-071c-463d-9851-5186d0253747` |
| Tagged session secret write | AccessDeniedException | `d8605af4-1904-4e1a-a7ae-9df12a25335f` |

CloudTrail event `1d96d28c-7e7a-4d7c-b94c-9ba3554a6c8e` records a successful
Decrypt at `2026-10-07T21:11:23Z` with the Acme bucket ARN as encryption context.
Denied GenerateDataKey calls are also visible (for example event
`946ca909-d4f5-4dee-81ae-8aec2597644f`). This confirms audit visibility while
Bucket Keys reduce repeated KMS calls; it does not imply one decrypt per S3 read.

Ohio `get-inference-profile` reported `us.amazon.nova-micro-v1:0` ACTIVE with only
`us-east-1`, `us-east-2`, and `us-west-2` model destinations. Exactly **three**
synthetic API calls invoked Bedrock using the service role:

| Call | HTTP | Input / output tokens | Evidence |
|---|---:|---:|---|
| Alice summarizes Bob, requesting compensation | 200 | 342 / 205 | Returned source fields exclude compensation; invocation `c8053895-61f8-4e41-8b5c-830eecae93ab` |
| Injection policy Q&A | 200 | 134 / 85 | Identified the instruction as untrusted; invocation `c48d40eb-dc65-4801-a7dc-30c5feba2937` |
| Handbook control Q&A | 200 | 121 / 75 | Abstained from inventing a policy; invocation `58d9f255-f3df-474f-acdc-4fa3ce0c39fb` |

The tenant usage ledger matches **3 attempts, 597 input and 365 output tokens**.
At the approved rates, generation is approximately **$0.000072**. Cross-tenant
AI input returned 404 and a model override returned 422 before those calls.
The live samples do not prove universal injection resistance or reveal model
prompt contents; permission-filtered prompt contents remain covered by the
reviewed deterministic local tests. No model output triggered tools or actions.

CloudWatch has KMS-encrypted logs with three-day retention. Observed JSON records
carry the supplied request IDs, and AI records carry invocation IDs without
prompt/answer fields. EMF request records use `[Service, Environment]`; the
existing dashboard and all three application alarms were present in `OK`.
Six metric series were visible at capture: four service-wide request metrics and
Acme input/output token metrics with `[TenantId, Service, Environment]`. The
20-series cost allowance is a baseline budget, not an assertion that every tenant
emitted every metric during this session.
No alarm state was forced. Historical metric existence is not an active leftover.

## WAF and private-path limits

The live ACL is associated with the public ALB and has `common`,
`known-bad-inputs` and `ip-rate` rules; sampled requests are disabled. A synthetic
XSS query returned 403. Legitimate 64-KiB and injection-fixture document uploads
both returned 201 with application authorization (request IDs
`3630d51269674bd480202b8a3c9060c4` and `831d32ac4d1e4a5c8245d9df4debc30c`).
The persistent-connection rate probe delivered 2,400 requests in 30 seconds
(initially 404); after a 30-second evaluation wait, all 20 follow-up requests
returned 403. The threshold remained 2,000/IP/300s throughout.

The leaf signature verifies against the local CA, with exactly the five tenant
SANs under `mockworkday.internal`; ACM reported ISSUED. The private zone has only
NS/SOA records, endpoint-service permissions are empty, and there are no endpoint
connections. With consumer inputs unset, **end-to-end PrivateLink, tenant DNS
resolution, remote CA/hostname validation, unapproved consumer connection tests,
cross-domain EventBridge delivery, encrypted DLQ retention/redrive and DLQ alarms
remain unverified**. No consumer resources were fabricated. Public-domain/ACM
DNS validation is also untested because `public_domain=null`.

## Isolated tenant deletion

Globex login succeeded before deletion. Owner SQL disabled tenant access,
accounts/clients and grants. New login and the previously issued token both
returned 404. The operator removed its one S3 object, both ASU secrets and all
Globex database rows, using tenant-scoped transactions and dependency order;
there was no event subscription to remove. Acme remained usable afterward.

Key `1f00b756-1c8c-4613-a67b-338a5243b54f` was disabled, verified Disabled, then
scheduled for seven-day deletion at **2026-10-14T21:26:46.204Z**. Scheduling remains
cancelable: final crypto-shredding cannot be proven before that date. This test
relies on API denial and object removal for immediate revocation, not on KMS
cache invalidation. It does not erase downloaded plaintext or demonstrate
immediate revocation of cached Bucket Keys.

## Failures and recovery

- Default Docker daemon unavailable: selected the already running Colima builder;
  no deployment-design change.
- Temporary evidence probes initially mishandled SQLAlchemy RowMapping JSON,
  counted ASUs as bulk human accounts, checked the account flag instead of ASU
  enablement, and compared different UUID string formats. Corrected the probes;
  final assertions passed without changing the service or seed data.
- The CloudTrail summary initially assumed non-null request parameters; corrected
  the local parser and retained the raw response.
- One report download hit a connection reset before the expiry check. A bounded
  retry run verified both initial success and expiry denial.
- The initial WAF load generator reached only 140–310 requests/minute, below
  the configured threshold; it was stopped and replaced with a bounded persistent-
  connection probe, without modifying WAF.
- The first `aws-down` destroyed all 110 remaining Terraform resources, then
  failed at ACM `DeleteCertificate` (CLI exit 254; the wrapper hid AWS stderr).
  CloudTrail subsequently confirmed `ResourceInUseException` at
  `2026-10-07T21:46:08Z` (event `a4499b70-a5c2-4de1-b18b-b7b033efe812`).
  A subsequent describe showed `InUseBy=[]`; retrying the existing TLS cleanup
  succeeded at `21:47:00Z` (event `4fa18a1b-6a79-45b4-a269-9e8e73dc4498`)
  and removed the local TLS files. The full down wrapper was rerun afterward.
- Two deletion ECS Exec sessions printed EOF after their success markers. Separate
  HTTP/AWS checks and the subsequent tenant deletion confirmed the operations.

## Cost basis

The reviewed provider baseline remains **$0.129331/hour**, before usage,
minimum billing increments and the temporary migration task. The 14,336 bulk
document PUTs alone correspond to about **$0.07168** at the approved S3 request
rate; Bedrock generation was approximately **$0.000072**. These are calculations
from [the reviewed rates](m3-plan.md), not a retrieved invoice. Other requests,
logs, transfer, migration and persistent registry/state storage remain additional.
The private zone is removed within the documented 12-hour deletion exemption;
pending-deletion KMS keys are informational and have no key-storage charge during
the waiting period. No full-month deployment cost is implied by this short run.

## Teardown and retained evidence

The first destroy removed **110 resources**. The isolated Globex deletion had
already removed two secrets and their two policies; Terraform refresh also
removed its pending-deletion key from managed state. TLS cleanup succeeded on
retry. The next `make aws-down` confirmed **0 resources left to destroy**, then
failed its account-wide inventory on the five entries below. The independent
`make aws-leftovers` repeated the same five-resource failure (non-zero exit).
`terraform state list` returned no entries. That original command verdict was
non-clean; the reviewer-approved resolution and successful recheck follow below.

| Remaining account resource | Ownership evidence |
|---|---|
| NLB `lab-s1-completion/1d55c7c8d510fc0d` | `lab=agent-runtime`, `experiment=s1` |
| EIP `eipalloc-01278bc37f07db2d3` | `lab=agent-runtime`, `experiment=s1`, `Name=lab-s1-trusted-host` |
| EBS `vol-005b2be2d664926a6` | `lab=agent-runtime`, `experiment=s1` |
| Log group `/lab/s1/dns` | `lab=agent-runtime`, `experiment=s1` |
| Log group `/aws/lambda/lab-dev-cost-guard` | `Project=lab-platform`, `Stack=cost-guard`, `lab=agent-runtime` |

These are outside this deployment and were not changed. The reviewer confirmed
four belonged to the live S1 experiment and the cost-guard log group is permanent
platform infrastructure. Decision D53 scopes the verdict to Mock Workday ownership
while preserving other-owner visibility and failure on inventory errors.

The updated shared checker is used by both `aws-down` and `aws-leftovers`.
It uses native service existence checks plus `Project=mock-workday` or service
name prefixes for the failure verdict. The separate **OTHER OWNERS** section
prints all returned `lab=agent-runtime` entries with owner tags; it also includes
the broader informational tag index, whose entries may be stale. No claim is made
that all index-only entries represent live resources.

During this update, an initial attempt also queried the tagging API in Virginia.
`tag:GetResources` was explicitly denied by SCP `p-6u76e4dy`; the command exited
nonzero. The implementation was corrected to retain D1's Ohio inventory plus
Virginia **Logs only**, reading log tags through the Logs API. No IAM or SCP
changes were made and no inventory error was suppressed.

The final live `make aws-leftovers` completed at **2026-10-07 22:09:53 UTC**
(2026-10-08 06:09:53 Asia/Shanghai) with **exit 0**, **0 REMAINS** entries,
**6 PendingDeletion** keys, and **63 informational other-owner entries** with
owner tags: 57 tagged `experiment=s1`, six platform cost-guard entries. All five
original blockers appear in that informational section. The broader count also
includes native IAM role tags and tag-index-only resources, not just the five
original billable-resource findings. Final output:

```text
No disposable/billable leftovers found for Mock Workday. Pending KMS deletions, if any, remain inventoried above. Platform state, network, SSM and registry are intentionally retained.
```

No AWS resources were changed for this follow-up. The previous successful destroy
stands; `aws-down` invokes this same checker. Acceptance is for the approved
provider-only scope: consumer-dependent checks described above remain unverified.

Local verification: `uv run --frozen pytest tests/test_leftovers.py tests/test_m3_infra.py -q` passed **19 tests**; Ruff and `git diff --check` passed.
Coverage includes S1-tagged resources being informational with tags, Mock
Workday-tagged resources failing, untagged owned names, name boundaries, an
owned bucket outside the naming prefix, stale tag entries, native other-owner
tags absent from the index, and failure on inventory/tag-read errors.

The Mock Workday inventory found no active service resources. Local TLS files are
gone and `private_certificate_arn` is null. Six service-owned keys remain as
**PendingDeletion**, informational, with the following scheduled dates (UTC):

| Key | Key ID | Scheduled deletion |
|---|---|---|
| globex | `1f00b756-1c8c-4613-a67b-338a5243b54f` | 2026-10-14 21:26:46.204 |
| acme | `4e95bfd1-0c66-4fc1-8b5d-57baeab17b53` | 2026-10-14 21:44:02.804 |
| meridian | `95fc8816-071b-4267-b9f9-c3ca031ae42b` | 2026-10-14 21:44:02.779 |
| cedar | `c9094ddb-11d1-4e41-a7db-c78c333b7832` | 2026-10-14 21:44:02.808 |
| northstar | `f1205a85-e70a-4e0c-b00c-b057141161b4` | 2026-10-14 21:44:02.781 |
| operational | `eae8a830-2e44-4bc4-b1ac-3532cde55646` | 2026-10-14 21:44:05.515 |

Historical EMF series, stale tagging-index entries, stopped tasks and inactive
task definitions were informational. ECR and platform state/foundation remain
intentional exemptions; their storage costs are not asserted to be zero.

### Unknown-owner regression follow-up

The reviewer identified that resources without either Mock Workday attribution
or the shared lab tag were being omitted. They now appear with kind and ID as
`UNKNOWN OWNER`; cost-bearing entries fail the shared down/leftovers checker.
Unknown ENIs/logs and metadata remain visible as informational entries for the
reasons recorded in the code and spec D53.

Live `make aws-leftovers` completed at **2026-10-07 22:16:30 UTC** with **exit 0**:
**0 owned leftovers**, **0 unknown-owner cost-bearing resources**,
**6 pending-deletion keys**, **34 other-owner entries** and
**16 informational unknown-owner metadata entries** (default event bus and IAM roles).
No resource was tagged, deleted or otherwise changed. Evidence:
`.local/m3-leftovers-unknown-owner.log`.

`uv run --frozen pytest tests/test_leftovers.py tests/test_m3_infra.py -q` passed
**25 tests**, including untagged NAT, EIP, load balancer, EBS, RDS and secret
failure cases. Ruff and `git diff --check` passed. Checkpoint 4 remains accepted.

Local evidence is in ignored `.local/m3-checkpoint4-*.log`, `m3-*-probes*.log`,
`m3-deployment.json`, `m3-s3-seed-inventory.json`, `m3-kms-cloudtrail.json`,
`m3-observability.json`, `.local/m3-leftovers-owned-scope*.log`, and specific
WAF/expiry/deletion logs. These artifacts
are not published because raw infrastructure outputs and signed capabilities
need separate handling. The sanitized results above are the committed evidence.
