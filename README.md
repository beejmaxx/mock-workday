# Mock Workday

A small, multi-tenant HCM service modeled on public Workday concepts: supervisory organizations, Position Management, role-based constrained security, domain permissions, effective dating, and audit.

**Status:** M1 and M2 implemented, including Change Job, Request Time Off, idempotency, and fault injection. AWS deployment is a separate, deferred milestone.

This is an independent learning project, not affiliated with or endorsed by Workday. It uses synthetic data only. Workday facts, Workday-inspired concepts, and lab policy are distinguished in the [specification](docs/spec.md) and [verification notes](docs/workday-verification.md).

## Test locally

Install `uv`, Python 3.12, and PostgreSQL 16+ (Homebrew PostgreSQL 17 is supported). On macOS:

```sh
brew install uv postgresql@17
uv python install 3.12
```

The fixture finds Homebrew PostgreSQL 17 under `/opt/homebrew/opt/postgresql@17/bin`;
for other installations, put `initdb` and `pg_ctl` on `PATH`. No Homebrew database
service needs to be started. Then run:

```sh
make test
```

The session fixture starts a separate temporary Postgres cluster, creates `mw_owner` and `mw_app`, applies `schema.sql`, and stops the cluster when pytest finishes. It does not touch an existing database or need Docker. Every test starts with fresh synthetic seed data. Test names carry the §11 acceptance IDs; parametrized cases use those IDs in their pytest case names.

The tests use real PostgreSQL permissions, forced row-level security, HTTP requests through both ASGI apps, and a clock that moves only through explicit set/advance calls. Audit-failure tests revoke database INSERT privileges and restore them in `finally` blocks.

## Run the containers

On macOS, install Homebrew, then the Docker CLI, Colima engine, and Compose plugin:

```sh
brew install docker colima docker-compose
```

Merge this setting into `~/.docker/config.json`, preserving any existing settings
(Homebrew prints the appropriate path during installation; on Apple Silicon it is):

```json
{
  "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]
}
```

On Intel Homebrew installations, use `/usr/local/lib/docker/cli-plugins` instead.
Start the engine and check that the CLI can find Compose:

```sh
colima start
docker context use colima
docker compose version
docker info
```

The container path needs `make` (Xcode Command Line Tools on macOS) and available
host ports (8080 and 8081 by default). It does not require host Python, uv, or PostgreSQL;
those are required only for `make test`. The first build needs network access to
fetch the Python/uv/PostgreSQL images and Python packages. Check for existing
listeners with `lsof -nP -iTCP:8080 -iTCP:8081 -sTCP:LISTEN` before starting:
Colima can start the containers even when another host process prevents port
forwarding. If a port is occupied, leave that process running and choose another
host port using `MW_PUBLIC_PORT` or `MW_ADMIN_PORT` (examples below).

With the engine running:

```sh
make up
# If port 8080 is occupied:
MW_PUBLIC_PORT=18080 make up
# Optional disposable test-admin interface:
MW_PUBLIC_PORT=18080 MW_TEST_ADMIN=1 make up

# Remove the containers and database volume:
make down
```

The image is tagged `mock-workday:m2`. Schema and seed are installed on the first startup of a fresh volume. `make down` removes that volume; the next `make up` reproduces the seed IDs. Signing keys and runtime-created records are regenerated.

`MW_PUBLIC_PORT` defaults to 8080; `MW_ADMIN_PORT` defaults to 8081. These are
Compose host-port settings only; container ports remain 8080 and 8081. Keep the
same overrides on subsequent `make up` commands, and use the selected public
port in HTTP requests. For example, `MW_PUBLIC_PORT=18080 MW_ADMIN_PORT=18081
MW_TEST_ADMIN=1 make up` uses host ports 18080 and 18081.

Both published ports bind to `127.0.0.1` only, not LAN interfaces. The admin
listener is enabled only with `MW_TEST_ADMIN=1`, even though its port mapping is
present in Compose. PostgreSQL is not published to the host.

Validated on macOS/Colima on 2026-10-07 with public port 18080: image build,
Alice login and worker GET, admin enablement and loopback isolation, injected
503, volume removal, and fresh startup. All 105 seed IDs across both tenants
matched after recreation.

Public API: `127.0.0.1:8080`. The tenant comes only from the HTTP Host header. No hosts-file change is needed:

```sh
curl --noproxy "*" -s http://127.0.0.1:8080/oauth2/token \
  -H 'Host: acme.mockworkday.local' \
  -d grant_type=password -d username=alice -d password=pw-alice
```

Use the returned access token with `Authorization: Bearer <token>` on `/api/v1/workers`, `/api/v1/documents`, and the other [OpenAPI endpoints](docs/openapi.json). The served contract is `/openapi.json`; interactive documentation is `/docs`.

Human passwords are `pw-<username>`. Client secrets are `secret-<client_id>` for `assistant`, `hr-assistant`, `directory-sync`, and `eng-sync`. All are synthetic lab credentials. The fixed Compose database credentials are documented in spec §10; there are no external secrets to configure.

The test-admin app exists only with `MW_TEST_ADMIN=1` and uses port 8081. Its routes never appear on the public app. With it enabled, time starts at `2026-10-07T09:00:00Z` and moves only through `/admin/clock`; otherwise time progresses from that seed instant. `/admin/reset` restores both tenants, the clock, keys, rate-limit state, and fault rules.

## Contract and boundaries

- Explicit tenant predicates plus PostgreSQL RLS enforce tenant isolation.
- Delegated access uses the human's current permissions intersected with token scopes, current client ceiling, and current grant scopes.
- Historical data reads use current authorization.
- Sensitive reads and writes require durable audit; audit failure rolls back and returns 503. Denial auditing is best effort.
- Document creation returns 201. Lists return metadata only; content is returned by the individual-document GET.
- Lists use signed keyset cursors and current authorization. Rate limits are in-process and per tenant/client or direct human account.
- Change Job and Request Time Off use current assignees, optimistic versions, and transaction-scoped locks. Final changes and audit records commit together.
- Business-process POSTs require `Idempotency-Key`. Successful retries return the original receipt; current authorization controls protected fields, and revoked grants or disabled clients prevent replay.
- One service process serves both ports. This small lab intentionally uses direct SQL and plain functions; pagination may inspect all candidate rows and is not optimized for large datasets.

Other systems consume the versioned HTTP contract and container image, plus the approved v1 business notifications and API-issued short-lived report URLs. There is no shared database or code interface.

## Documents

- [Implementation contract](docs/spec.md)
- [Public OpenAPI document](docs/openapi.json)
- [Original plan](docs/mock-workday-plan-claude.md)
- [Workday evidence and deliberate simplifications](docs/workday-verification.md)

## AWS dev (MW-D1)

The [D1 spec](docs/d1-aws-dev.md) owns the deployment contract. This repository
contains only the registry and disposable service stacks. The platform repository
owns the state bucket and foundation; deploy those separately before planning here.
No foundation Terraform state is read: network IDs come only from
`/lab/dev/network/vpc_id`, `/lab/dev/network/public_subnet_ids`, and
`/lab/dev/network/private_subnet_ids` in SSM.

Prerequisites: Terraform 1.10+, AWS CLI v2 with a valid `agent-runtime` profile for
dev account `729608197929`, Docker/Colima with ARM64 builds, Python 3, curl, and make.
For example, install Terraform with `brew install hashicorp/tap/terraform`.
The AWS region is fixed to `us-east-2`; scripts honor `AWS_PROFILE` and default it
to `agent-runtime`, and refuse another account. ECS Exec additionally needs the
AWS Session Manager plugin on the operator's machine.

The encrypted S3 backend is `beejmaxx-lab-tfstate-dev`, with native lock files and
keys `dev/mock-workday-registry.tfstate` and `dev/mock-workday-service.tfstate`.
AWS provider `~> 6.0` and committed provider lock files keep installs reproducible.
Before the bucket/foundation exist, validate without connecting a backend:

```sh
terraform -chdir=infra/envs/dev/registry init -backend=false
terraform -chdir=infra/envs/dev/registry validate
terraform -chdir=infra/envs/dev/service init -backend=false
terraform -chdir=infra/envs/dev/service validate
terraform fmt -check -recursive infra
```

After the platform is available:

```sh
make aws-plan       # prompts for an IPv4 /32; default is your direct public egress IP
# Stop here for user review of the plans. The following commands create/delete resources:
make aws-up         # interactive Terraform applies, ARM64 build/push, migration, health wait
make aws-smoke      # 200 worker read, 403 compensation, 404 cross-tenant, 401 revoked grant
make aws-down       # interactive destroy of SERVICE ONLY, then leftover inventory
make aws-leftovers  # read-only inventory; nonzero if leftovers remain
```

After the deployment has been approved, hands-off commands are available:

```sh
bash infra/scripts/aws.sh up --yes
bash infra/scripts/aws.sh down --yes
# Alternatively, explicitly supply the current detected direct egress /32:
MW_ALLOWED_CIDR=203.0.113.7/32 make aws-up  # replace with your actual direct IP
MW_ALLOWED_CIDR=203.0.113.7/32 make aws-down
```

`--yes` or a set `MW_ALLOWED_CIDR` skips CIDR/Terraform prompts and enables
`-input=false -auto-approve` for apply/destroy. Configuration always detects the
direct egress IPv4 and uses its `/32`; a supplied CIDR must match exactly (empty,
stale or wider values are refused). Down reuses existing tfvars; it only detects
an address when those inputs are missing. Account checks still run. Interactive
mode remains the default, and these switches do not replace deployment review.

TLS cleanup retries ACM deletion up to six times, waiting 2/4/8/16/30 seconds
between attempts after a detach race. It prints AWS error codes, stops on
permission/invalid-request failures, and retains local TLS files and the ARN
until deletion succeeds (an already-absent certificate is success).

API requests must bypass HTTP proxies: the `Host` header selects the tenant, and
an HTTP proxy may rewrite it and break tenant routing. For manual requests, use
`curl --noproxy "*"` and `-H 'Host: acme.mockworkday.local'`. IP detection also uses
`curl --noproxy "*"`, matching `aws-smoke`, which bypasses proxies. Thus the default
ALB /32 is your direct egress IP, not the proxy's IP, even when `HTTP_PROXY`,
`HTTPS_PROXY`, or `ALL_PROXY` is set.

`aws-plan` and `aws-up` save non-secret deployment inputs in the gitignored
`.local/aws-dev.tfvars.json`. `MW_IMAGE_TAG` overrides the default Git commit tag.
The module also accepts an image digest, sizing, database backup/deletion policies,
secret recovery window, log retention, name, account,
region and network inputs. `enable_test_admin` and `enable_exec` default to false;
the dev root explicitly enables them. Neither the module nor these scripts create
a VPC, NAT gateway, endpoints, or the platform state bucket.

The service has one ARM64 Fargate task, private PostgreSQL 17, and an HTTP ALB
restricted to the chosen /32. Only port 8080 reaches the load balancer. The
one-off migration task receives the RDS master password; the long-running service
receives the app password, plus the owner password only when test admin is enabled.
With test admin disabled, the app creates no owner database engine. It starts the
application directly without running the privileged bootstrap. Bootstrap creates missing roles, updates their
passwords, and installs/seeds a fresh database without reseeding an existing one.
The ALB health check uses `/openapi.json`; the migration must also succeed before
`aws-up` reports success. Use `aws-smoke` to verify database-backed behavior.

Application environment variables (Compose defaults preserved):

| Variable | Default / use |
|---|---|
| `MW_DB_HOST` | `db` |
| `MW_DB_PORT` | `5432` |
| `MW_DB_NAME` | `mock_workday` |
| `MW_DB_OWNER_PASSWORD` | `mw-owner-lab`, for fixed role `mw_owner` |
| `MW_DB_APP_PASSWORD` | `mw-app-lab`, for fixed role `mw_app` |
| `MW_DB_ADMIN_USER`, `MW_DB_ADMIN_PASSWORD` | Optional, migration only; master connects to the existing database |

Secrets are injected by ECS from Secrets Manager; do not put passwords in tfvars
or shell arguments. Terraform state contains generated owner/app passwords, so
keep the backend private. The task role has only the four
[documented ECS Exec channel permissions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-iam-roles.html#ecs-exec-required-iam-permissions).
To check admin access after deployment, choose the running service task and use:

```sh
aws --profile agent-runtime --region us-east-2 ecs execute-command \
  --cluster mock-workday-dev --task <service-task-arn> --container mock-workday \
  --interactive --command 'curl -fsS http://localhost:8081/openapi.json'
```

Budget roughly **$0.07–0.08/hour while running**, the D1 planning estimate for
Fargate, RDS/storage, ALB/usage, and public IPv4 addresses. Actual use and pricing
vary; see [Fargate](https://aws.amazon.com/fargate/pricing/),
[RDS](https://aws.amazon.com/rds/postgresql/pricing/),
[ALB](https://aws.amazon.com/elasticloadbalancing/pricing/), and
[IPv4 pricing](https://aws.amazon.com/vpc/pricing/). **Run `make aws-down` after
study sessions.** RDS provisioning may take several minutes. Destroying the
service retains the registry and platform; image/state storage can still incur
small charges. The leftover checker scans the dev account for the listed resource
classes (including untagged resources with Mock Workday names and logs in
`us-east-1`). It fails for Mock Workday-owned leftovers, unknown-owner cost-bearing resources,
or inventory errors;
other `lab=agent-runtime` owners appear separately with their tags. Service-specific list/describe calls determine
failure; tag-only findings are informational warnings and do not fail the check.
STOPPED tasks and task definitions do not count as active leftovers. Owned target
groups, ECS clusters/services and unattached ENIs still fail the check.
It never deletes anything automatically.


## M3 slice 2a: bulk data and tenant storage

The approved [M3 spec](docs/m3-spec.md) is implemented in reviewable slices.
Slice 2a adds storage/session code, a deterministic bulk generator and balance
reads. AWS provisioning/policy validation remains at checkpoints 3/4.
Existing databases must be recreated with the new `schema.sql`; bootstrap does
not migrate an already-installed D1 database.

`make up` and `make test` keep the original small fixtures. Run `make seed-bulk`
after local startup to add Northstar, Meridian and Cedar. `make test-bulk`
loads and verifies all three in throwaway PostgreSQL, with no Docker/AWS calls.
The three tenants add 3,584 workers, 299 orgs, 4,200 positions, 10,752 business
processes and 14,336 documents. The full PostgreSQL test measured **146,761,728
bytes (139.96 MiB)** of document text and **72,584,339 bytes (69.22 MiB)** for the local database with the richer
templates. PostgreSQL compresses this synthetic prose; this is not production
sizing evidence.
Bulk usernames are `worker-00001`, etc.; each
tenant shares synthetic password `pw-bulk-<slug>` and one computed password
hash. Never use these credentials for real data.

The CLI saves complete per-document manifests under `/tmp/mock-workday-bulk-manifests`
by default (ephemeral with the container). For a selected tenant or another
manifest directory, run inside the service container:

```sh
.venv/bin/python -m mock_workday.bulk_seed --tenant northstar --manifest-dir /tmp/bulk-manifests
```

The same command runs in the one-off bootstrap image against RDS/S3 at approved
deployment. It verifies matching rows/bodies on rerun, resumes incomplete imports,
and rejects modified fixtures. It never publishes events or invokes a model.
Partial imports are unavailable through tenant routing. There is no automatic
reset; the existing explicit test-admin reset restores the small dataset. It
also removes bulk DB records; delete their S3 objects during owner cleanup or
teardown. Import ASU fixtures only when slice 2b is implemented.

`GET /api/v1/workers/{id}/time-off-balances?as_of=2026-10-07` returns authorized
read-only VACATION snapshots, with `asOf`, granted/taken/remaining hours. Live
time-off approvals do not debit these seed snapshots.

Local bodies remain in PostgreSQL. To select AWS storage, provide both
`MW_TENANT_DATA_ROLE_ARN` and `MW_TENANT_STORAGE`, a JSON map from hyphenated tenant
UUIDs to `{"bucket":"...","kms_key_id":"..."}`. All configured cloud access
uses the AWS credential chain and per-tenant tagged STS sessions. S3 puts are
immutable, SSE-KMS encrypted and request Bucket Keys. A missing tenant mapping
or failed cloud request cannot fall back to local bodies. No bucket/key/role
is accepted from an API caller. Actual AWS policies/resources are not installed
by this slice.

An owner can inventory up to 1,000 document keys per call; only objects at least
24 hours old with no committed metadata qualify as orphans. This excludes fresh
in-flight uploads and serializes against bulk import. Use `next_token` as
`--starting-token` to continue. The default is read-only; explicitly add
`--delete-orphans` to delete the returned candidates:

```sh
.venv/bin/python -m mock_workday.storage --tenant <tenant-uuid>
```

Run cleanup before deleting the tenant registry record; stack teardown handles
buckets left by a full database reset. Manifests/inventories contain synthetic
IDs, sizes and hashes, never credentials. Cloud-side integration and teardown
proofs remain deferred until the approved deployment checkpoint.

### M3 slice 2b: ASU identities and credentials

[Identity contract](docs/spec.md#36-m3-asu-identity-and-credentials-lab-policy-unless-stated)
and [isolated admin OpenAPI](docs/admin-openapi.json) describe the new endpoints.
Recreate the disposable database for the expanded schema. Existing small seeds
and legacy HUMAN/ISU clients remain unchanged. After seeding tenants, explicitly
run `uv run python -m mock_workday.identity` to add disabled synthetic
registrations and two ASUs each; this command creates no usable credentials.

Set `MW_CREDENTIAL_STORE` to a private local JSON file path (default
`/tmp/mock-workday-credentials/credentials.json`) or `aws`. The file is mode 0600;
keep it outside the checkout and remove it when tearing down the local lab.
The isolated port-8081 admin API can enroll local credentials. Enable both the
registration and mode explicitly; configure scopes and operationIds narrowly.
Delegate enrollment returns a synthetic secret once. A human creates the grant,
then the delegate exchanges its client secret, credential version and grant ID
without another human token. Ambient enrollment accepts only the public X.509
certificate; its private signing key never belongs in Mock Workday.

For AWS, after checkpoint-4 approval and secret-container provisioning, use
`MW_CREDENTIAL_STORE=aws uv run python -m mock_workday.identity --references /private/asu-references.json`
with a JSON object mapping each existing tenant slug to `DELEGATE` and `AMBIENT`
secret ARNs. Run credential preparation under the owner/bootstrap IAM role:

```sh
MW_CREDENTIAL_STORE=aws uv run python -m mock_workday.credentials \
  --tenant TENANT_UUID --asu ASU_UUID --mode DELEGATE \
  --reference SECRET_ARN --output /private/new-credential.json
```

For ambient mode add `--certificate /private/public-certificate.pem`. The output
file must not exist; it is created mode 0600 and contains the version ID plus
only the newly generated delegate secret, when applicable. Deliver that secret
over an authorized encrypted operator channel. Through the isolated admin API,
activate the ID using `existing_version`. Set the controllable service clock to
current UTC before live certificate enrollment. Do not send enrollment secrets
through D1's public HTTP ALB. A lost one-time result requires rotation.

The app reads exact secret versions using tenant-tagged sessions; it cannot
write AWS secret values. Database status controls acceptance, not Secrets
Manager staging labels. A failed activation can leave an inert version for
owner cleanup. Emergency revocation invalidates existing tokens immediately;
AWS old secret material awaits owner cleanup, while local removal is attempted
after commit. Credential hashes, keys and tokens never enter registry rows or
request logs. Structured JSON request logs carry `X-Request-Id`; the 3-day
CloudWatch retention/encryption and live IAM proofs belong to checkpoints 3–4.

### M3 slice 2c: report exports and business notifications

`POST /api/v1/report-exports` accepts
`{"report":"worker-roster","include_compensation":false}` with optional
`as_of`. It returns snapshot metadata and a GET-only download capability after
current authorization and audit commit. The synchronous NDJSON report is capped
at 10,000 rows / 16 MiB. Compensation appears only when both requested and
permitted per row. See [the report contract](docs/spec.md#67-report-exports-m3-slice-2c).

URLs last at most 60 seconds, further capped by token/grant/credential and AWS
session expiry. Anyone holding one can reuse it until expiry; grant revocation
does not instantly revoke it. Keep URLs out of logs and untrusted documents.
AWS downloads go directly to S3 over HTTPS with no consumer AWS credentials.
Local downloads use an HMAC-signed URL on the public app; restart invalidates
outstanding local URLs. Creation responses and local downloads use `no-store`.
The one-day S3 lifecycle backstop belongs to checkpoint 3. Locally, run the
owner-only `uv run python -m mock_workday.reports --tenant UUID` to clear bodies
older than one day; metadata/audit remains. Recreate the disposable database for
this expanded schema; it is not an in-place migration.

Business transitions now commit an outbox row with BP history and the existing
idempotency receipt. The executable starts one dispatcher thread in the same
service process. Set `MW_EVENT_BUS_ARN` only to the provisioned Mock Workday bus;
without it, dispatch captures the most recent 1,000 notifications locally and
makes no AWS call. Seed commands/app-factory tests never start dispatch or publish
seeded history. The service IAM role will receive `events:PutEvents` only on its
own bus in checkpoint 3. AWS service configuration must set the standard
`AWS_DEFAULT_REGION=us-east-2` for boto3 and regional S3 signing.

The [v1 event detail schema](docs/business-event-v1.schema.json) covers job-change
submission/final approval and time-off final approval. Consumers deduplicate by
`(tenant_id,event_id)` and tolerate out-of-order BP versions. Notifications carry
no authority; consumers refetch through HTTP using current credentials. No
compensation, comments, reasons, documents or capabilities are included. See
[the outbox contract](docs/spec.md#57-business-notifications-and-transactional-outbox-m3-slice-2c).

The outbox retries failed/unknown results with bounded exponential backoff.
A crash after acceptance can repeat the same stable event ID. Structured logs
report pending counts/oldest age. Owner repair commands preserve those IDs:

```sh
uv run python -m mock_workday.events --tenant UUID republish \
  --start 2026-10-07T00:00:00+00:00 --end 2026-10-08T00:00:00+00:00
uv run python -m mock_workday.events --tenant UUID prune
```

Repair is audited, limited to 100 history transitions over at most seven days,
and schedules ordinary dispatcher delivery. Pruning removes only rows published
more than seven days ago; pending intent never ages out. The approved provider
DLQs, tenant-filtered cross-domain rules, encryption and alarms are Terraform
checkpoint-3/4 work. The other domain owns its receiving bus and consumer queue.
After fixing a target-delivery failure, an owner may explicitly run:

```sh
uv run python -m mock_workday.events --tenant UUID redrive --queue-url PROVIDER_DLQ_URL
```

This requires `MW_EVENT_BUS_ARN` and the owner role's narrowly scoped provider-DLQ
receive/delete permissions. It reads at most ten messages, verifies schema,
source/account/tenant and committed history, audits the attempt, and deletes only
after confirmed republication. Unknown results remain for retry. Inspect DLQ
failure attributes before redriving; no automatic redrive, SNS or consumer-side
queue is added. A successful PutEvents result does not prove target delivery;
checkpoint 4 must verify the bus exists and inspect target/DLQ behavior.

### Native AI (M3 slice 2d)

`POST /api/v1/ai/worker-summary`, `/team-summary`, `/document-qa` and `/generate`
use current caller permissions before assembling model input, then recheck them
before returning advisory text. See [the AI contract](docs/spec.md#68-native-ai-and-metering-workday-inspired-features-lab-policy-implementation)
and [OpenAPI](docs/openapi.json) for bodies, sources and limits. Fresh disposable
schema installation is required for the AI ledger tables and seed entitlements.

Local runs default to `MW_AI_BACKEND=fake`: fixed output/usage, no cloud access.
Pytest forces this mode; Bedrock contract tests use Stubber. An ordinary human
login can try `{"prompt":"Write a synthetic greeting"}` at `/api/v1/ai/generate`.
Integrations need explicit `ai` scope, membership in `Native AI Callers`, and
separate data permissions; delegates use their human's current entitlements.
Existing client ceilings do not gain AI access automatically.

The PostgreSQL ledger reserves budget before inference, counts uncertain calls
conservatively and recovers stale concurrency leases after 60 seconds. Logs and
EMF contain IDs, timing and usage, never prompt/answer content. `MW_AI_BACKEND=bedrock`
uses the task role and the approved Nova Micro US profile; enable it only during
approved AWS deployment. IAM, dashboard/alarms, log retention and live model
access verification remain checkpoint-3/4 work. No AWS resources were created
for slice 2d.


### M3 infrastructure (checkpoint 3)

[Plan report and cost check](docs/m3-plan.md) describe the provider-only plan,
conditional consumer resources and TLS enrollment. The service baseline is about
$0.129331/hour before usage with consumer inputs unset; the §8 full configuration
including a one-AZ consumer endpoint is about $0.139742/hour.

Optional `.local/aws-dev.tfvars.json` keys are `public_domain` (default null),
`allowed_principal` (account-root ARN only), `consumer_vpc_id`,
`consumer_endpoint_id`, `consumer_endpoint_dns`, `target_event_bus_arn`, and
`event_tenant_slugs` (explicit allowlist when a target is supplied). The plan
wrapper preserves these keys. Do not put credentials, private keys or bodies
in tfvars. The imported `private_certificate_arn` is supplied by owner enrollment
at approved deployment; null means the private TLS listener is not active.
Runtime endpoint/VPC association and receiver bus/queue remain consumer-owned.

Checkpoint 3 ran no deployment or cleanup commands. The updated `aws-up` and
`aws-down` paths are for checkpoint 4 only, after plan approval. Recheck the
operator /32, image tag and certificate plan before applying.

### M3 live provider validation (checkpoint 4)

The approved `5fa0502` deployment and validation are recorded in
[the live validation report](docs/m3-validation.md), including failures and the
consumer-dependent checks that remain unverified. Mock Workday service teardown
succeeded. Checkpoint 4 is accepted after the approved owner-scoped inventory
passed; live S1 resources and permanent platform logs are informational other
owners and were left untouched. Six KMS keys remain scheduled for deletion.
`make aws-up` (using the
existing `DOCKER_CONTEXT=colima-s1-builder`) ran TLS enrollment/storage, bootstrap,
all three bulk imports and disabled-ASU seeding; `make aws-smoke` passed.

The AWS bulk dataset has 3,584 workers and 14,336 S3 documents totaling
146,761,728 bytes. Manifest hashes, all generated table counts and S3 inventories
matched. Measured RDS database size after validation was 45,094,579 bytes;
allocated RDS storage remains 20 GB. The migration task took about 10m50s.
The separate ASU seed adds two non-human accounts per tenant beyond bulk manifest
human-account counts. Original small fixtures were preserved until the deliberate
isolated Globex deletion check. Three native-AI calls consumed 597 input and 365
output tokens. This is lab evidence; it does not describe Workday infrastructure.
