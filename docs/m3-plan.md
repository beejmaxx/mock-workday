# MW-M3 checkpoint 3 — Terraform plan review

**Lab policy.** Checkpoint 2 is approved. This checkpoint extends D1 in the same
service state, with no apply, destroy, image push, certificate import, secret
write, model invocation or consumer resource creation. The platform repository,
bootstrap/foundation resources and state were not modified. Network discovery
uses only the three published SSM parameters; subnet descriptions provide AZ IDs.

## Inputs and deployment stages

The reviewed provider plan keeps `public_domain`, `allowed_principal`,
`consumer_vpc_id`, `consumer_endpoint_id`, `consumer_endpoint_dns`, and
`target_event_bus_arn` null; `event_tenant_slugs=[]`. The existing operator /32 is
retained. `private_certificate_arn` is also null because enrollment has not run.

- The provider NLB, IP target group and endpoint service exist independently of
  consumer inputs. The service requires explicit acceptance. Allowed principals
  accept **only `arn:aws:iam::<account>:root`**; role/user ARNs and wildcards fail
  input validation. Account permission permits connection requests, not automatic
  acceptance. A separately supplied endpoint ID is the only connection accepted.
- The private zone is initially associated with the provider VPC (a private zone
  needs an initial association). Consumer VPC input creates only provider-side
  association authorization. The consumer owner completes/removes its association.
  Exact five-tenant CNAMEs appear only with the endpoint DNS/ID and VPC handoff.
  The consumer endpoint/SG and receiver bus/queue are never created here.
- NLB direct ingress is closed. PrivateLink inbound SG evaluation is off; accepted
  connections and consumer SGs are the front-door controls. Cross-zone forwarding
  is on across the two provider AZs; task ingress from either LB is only 8080.
  No endpoint-service private DNS verification is attempted for `.internal`.
- A target bus plus a nonempty explicit tenant allowlist creates one forwarding
  rule, target, sending role/policy, provider KMS-encrypted 14-day DLQ, queue policy
  and three alarms. With no bus, none are created. Source and detail type match
  the published event contract; retries are 86,400 seconds / 185 attempts.
- Null public domain retains HTTP /32 access with Host headers. Non-null creates
  a public zone, five aliases, DNS validation, non-exportable wildcard ACM cert,
  HTTPS listener and HTTP redirect. Registrar delegation is the owner's action.

**TLS staging is explicit.** The spec requires private key values to stay outside
Terraform. No dummy certificate ARN is in the deployable plan. The provider plan
therefore has no private TLS listener or private target attachment yet. After
checkpoint-4 approval, `tls.py enroll` generates the 90-day CA/30-day leaf locally
(mode 0600), imports the leaf into ACM and writes its ARN to local tfvars. A fresh
plan then adds **one** TLS listener and attaches that target group to the existing
ECS service. The apply prompt shows that change before deployment. After apply,
`tls.py store` writes the leaf/key to the operational secret. No CA private key is
uploaded. Share only the public CA and non-secret fingerprint/host inventory;
clients must verify CA, expiry, SNI and Host. TLS ends at the NLB, then SG-isolated
HTTP reaches the task. The private path is **not ready** in the bootstrap plan.

The checked-in host resolver now accepts the exact configured public suffix and
`mockworkday.internal` for known tenants; canonical `.local` JWT issuer/audience
and assertion audience remain unchanged. Local alias tests cover this boundary.

## Plan and validation evidence

Validated with Terraform 1.16.4 and the locked AWS 6.67.0 provider (random 3.9.1):

| Plan | Add | Change | Destroy |
|---|---:|---:|---:|
| Registry (retained D1 ECR) | 0 | 0 | 0 |
| Provider-only service, all optional inputs null | **114** | **0** | **0** |
| Synthetic conditional review (TLS, consumer and public domain branches) | 143 | 0 | 0 |

The operator-principal correction was replanned against the same provider inputs:
**114 add, 0 change, 0 destroy**, with the same resource addresses and actions.
Comparing saved plan JSON after substituting the old operator ARN with the verified
IAM user ARN yields identical managed-resource changes. Only the TLS secret policy
has a fully known changed value at plan time; the other affected policies contain
resource ARNs that remain unknown until apply. Their source changes only rename
the operator reference. No resource, consumer input, or cost assumption changed.
The correction passed recursive fmt, both module-root validations, shell syntax,
and `uv run --frozen pytest -o addopts='' -q`: **233 passed**, including the four
bulk tests (one dependency deprecation warning). Seven isolated Terraform plan
checks of the module's validation accepted the user and a path-bearing role and
rejected `*`, `?`, another account, account-root and an STS session ARN. Those
checks used only the extracted variable definitions, without AWS providers or
resources. No apply, destroy, secret write or push was run.

The provider count is **112 AWS resources + 2 random_password resources**.
Private TLS activation adds one listener (115 total on a fresh full-provider plan)
and changes the existing task target attachment. With one fully specified consumer
but public_domain still null, the conditional total would be 132; its endpoint,
VPC association and receiver bus/queue remain outside this repository. The fully
conditional 143 count includes eleven optional public-domain resources.

`terraform fmt -check -recursive infra`, both `terraform validate` calls and both
real-state plans succeeded. The conditional review uses `-refresh=false` and
clearly synthetic consumer/domain/certificate values; no deployment accepts those
as real inputs. Local host/isolation and leftover tests passed (11); a separate
mocked owner TLS custody/cleanup test passed. Ruff and shell syntax checks passed.
No local test invoked AWS. Provider-plan inspection confirms no consumer target,
DLQ, public certificate/record or interface endpoint resources, no forbidden
NAT/CloudFront/Cognito/SNS/Private CA, explicit acceptance, seven-day key deletion,
and private single-AZ RDS. Both plans are additions only; platform resources are
not imported into this state.

Reproduce with the existing initialized backend:

```sh
terraform fmt -check -recursive infra
terraform -chdir=infra/envs/dev/registry validate
terraform -chdir=infra/envs/dev/service validate
AWS_PROFILE=agent-runtime terraform -chdir=infra/envs/dev/registry plan
AWS_PROFILE=agent-runtime terraform -chdir=infra/envs/dev/service plan \
  -var-file=../../../../.local/aws-dev.tfvars.json
```

 Raw plans,
plan JSON and price snapshots are local review artifacts under `.local/`, not
version-controlled. Plans may contain sensitive state and must not be published.
A separate **non-deployable synthetic-input** plan exercises conditional branches;
it is not a request to create a domain or resources in the runtime domain. Its
saved binary plan was removed after inspection to prevent accidental application;
text/JSON evidence remains locally. The provider saved plan remains available.

The service consists of the D1 task/database/public ALB plus five tenant buckets,
five tenant keys, one operational key, ten empty ASU secret containers, one empty
TLS secret, tagged tenant-data role, separate bootstrap task role, NLB/endpoint
service/private zone, own EventBridge bus, WAF, one dashboard and three application
alarms. Custom EMF metrics are emitted by the application, not Terraform resources.
The RDS-managed master secret is implicit in the DB resource, additional to the
13 explicit secret resources (10 ASU + TLS + two application DB passwords).

Every tenant bucket blocks public access, disables ACLs, requires TLS/exact SSE-KMS
key on writes, enables Bucket Keys and expires exports after one day. ABAC restricts
bucket/prefix, secret names, and key use to the fixed tenant tag. Key policies also
restrict S3 to the bucket encryption context (Bucket Keys) and secret use to that
tenant's Secrets Manager encryption context. The base app role has no direct
S3/secret/decrypt permission. The bootstrap role may assume tagged storage sessions,
but does not publish events or invoke models. The provider bus denies unrelated
producers. ASU secret writes and TLS secret access are owner-only. Queue draining
is owner-only. `operator_principal_arn` defaults to
`arn:aws:iam::729608197929:user/lab-operator-cli`, verified with
`AWS_PROFILE=agent-runtime aws sts get-caller-identity`. Both the dev input and
module validate an exact IAM user or role ARN in the provider account; wildcards,
STS session ARNs, account-root and other-account principals are rejected. This
operator input is separate from PrivateLink's account-root-only permission.
`tls.py`, `prepare_down.py`, `m3_leftovers.py`, and `aws.sh` use the CLI profile
without assuming a role-shaped caller ARN; the shell account check compares only
the account ID. No platform IAM identity was modified.

WAF is public-ALB-only: CommonRuleSet, KnownBadInputsRuleSet, and 2,000/IP/300s.
Only SizeRestrictions_BODY is counted instead of blocked. Sampled requests and
request-body logging are off. The log group uses the operational key and 3-day
retention. Alarms have no actions or SNS. Bedrock grants only InvokeModel on the
selected US profile and the three destination model ARNs, with the foundation
model statements conditioned to that profile.

IAM source-call inventory was generated locally with:

```sh
DISABLE_IAM_POLICY_AUTOPILOT_TELEMETRY=true uvx iam-policy-autopilot@latest generate-policies \
  /Users/bijan/code/mock-workday/src/mock_workday/storage.py \
  /Users/bijan/code/mock-workday/src/mock_workday/credentials.py \
  /Users/bijan/code/mock-workday/src/mock_workday/ai.py \
  /Users/bijan/code/mock-workday/src/mock_workday/events.py \
  /Users/bijan/code/mock-workday/src/mock_workday/reports.py \
  --region us-east-2 --account 729608197929 --pretty
```

Its baseline includes optional Bedrock tools/guardrails/bearer-token actions and
broad dynamic ARNs. Those are not the approved contract and were not adopted.
Terraform encodes the approved split-role/ABAC/resource-policy design instead.
Static inventory and a successful plan do not prove live IAM/SCP authorization.

## Cost check against M3 §8

Rechecked regional AWS offer files on 2026-10-08: ECS/ELB/SQS publication September
11, VPC September 17, S3 September 28, RDS/Bedrock October 6, CloudWatch October 7.
Rates agree with §8; no free-tier credits are subtracted. Sources are the regional
[offer links in §8](m3-spec.md#81-price-basis-and-baseline), plus current public
[KMS](https://aws.amazon.com/kms/pricing/),
[Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/),
[Route 53](https://aws.amazon.com/route53/pricing/),
[CloudWatch](https://aws.amazon.com/cloudwatch/pricing/), and
[WAF](https://aws.amazon.com/waf/pricing/) pricing.

| Provider baseline item | Hourly equivalent | 730-hour month |
|---|---:|---:|
| ARM Fargate 0.25 vCPU / 0.5 GiB | $0.009875 | $7.21 |
| RDS micro + 20 GB gp3 | $0.019151 | $13.98 |
| Public ALB + internal NLB base | $0.045000 | $32.85 |
| Three public IPv4 addresses | $0.015000 | $10.95 |
| Six KMS keys | $0.008219 | $6.00 |
| Fourteen secrets including RDS-managed master | $0.007671 | $5.60 |
| Private hosted zone | $0.000685 | $0.50 |
| S3 Standard 1 GB | $0.000032 | $0.023 |
| Three application alarms | $0.000411 | $0.30 |
| Twenty EMF series | $0.008219 | $6.00 |
| One dashboard | $0.004110 | $3.00 |
| WAF ACL + three rule references | $0.010959 | $8.00 |
| **Provider total** | **$0.129331** | **$94.41** |

The §8 full baseline is $0.139742/hour ($102.01/month): add the consumer's one-AZ
endpoint ($0.01/hour) and three target/DLQ alarms ($0.30/month). A supplied public
domain adds $0.50/month. TLS listener/certificate enrollment has no fixed hourly
increment. Empty secret containers may cost less before enrollment; the budget
conservatively includes all fourteen secrets.

Usage is additional: ALB $0.008/LCU-hour, NLB $0.006/NLCU-hour, S3 requests, KMS,
Secrets Manager, EventBridge, SQS, logs, WAF requests and transfer. Nova Micro
Standard remains $0.000035/1K input and $0.000140/1K output tokens (no batch/cache
assumption). Temporary migration tasks, CPU credits and persistent ECR/state
storage are excluded from this baseline. Route 53 is not generally prorated
(zone deletion within 12 hours has the documented exemption); endpoint partial
hours round up. Pending-deletion KMS keys are inventoried for their seven-day
waiting period, not reported as active key-storage spend.

## Deployment and teardown scripts (not executed here)

`aws-plan` preserves optional inputs instead of replacing them with D1-only vars.
`aws-up` enrolls TLS outside Terraform, applies the reviewed service, stores the
leaf, then runs bootstrap, the three bulk imports and disabled identity seeding
in one owner migration task. Actual ASU credential enrollment/activation remains
a separate owner action. It never enables identities or creates runtime credentials.

Before destroy, `prepare_down.py` refuses remaining consumer endpoint connections
or VPC associations, records DLQ counts, scales the API to zero and aborts pending
multipart uploads. Terraform empties its own buckets including versions, destroys
service resources and schedules keys for seven-day deletion. Only after listener
removal does TLS cleanup delete the imported ACM certificate and local private
files. The registry, platform state and foundation remain intact.

Leftover inventory adds owned buckets/versions/uploads, KMS keys/aliases/dates,
certificates, private/public zones and associations, endpoint connections,
event buses/rules/targets, DLQs, alarms/dashboard, WAF associations and IAM roles.
Inventory failures propagate. Historical EMF series and pending key-deletion dates
are explicitly informational; enabled/disabled unscheduled keys fail cleanup.
Consumer-owned cross-account resources cannot be declared clean solely from our
inventory: require the owner's deletion evidence at checkpoint 4. No cleanup
command was run at checkpoint 3.

Live checkpoint-4 proof still includes WAF/SCP permissions, CA trust/private DNS,
wrong/missing STS tags, KMS/secret rejection, real encrypted DLQ delivery and alarm
behavior, Bedrock profile access and authorized synthetic inference, bulk import,
crypto-shredding and final teardown inventory. These are not implied by plan success.

## Checkpoint 4 execution

The user approved `5fa0502` and checkpoint 4 on 2026-10-08. The fresh deployment
plan was reviewed before apply: 115 creates, exactly the provider-only plan plus
the enrolled TLS listener and expected ECS target attachment. The live results,
probe failures/recovery, consumer-dependent gaps and teardown evidence are in
[the checkpoint-4 validation report](m3-validation.md). Earlier statements in this
document about unexecuted deployment describe checkpoint 3, not that later run.
