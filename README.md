# Mock Workday

A small, multi-tenant HCM service modeled on public Workday concepts: supervisory organizations, Position Management, role-based constrained security, domain permissions, effective dating, and audit.

**Status:** M1 and M2 implemented, including Change Job, Request Time Off, idempotency, and fault injection. AWS deployment is a separate, deferred milestone.

This is an independent learning project, not affiliated with or endorsed by Workday. It uses synthetic data only. Workday facts, Workday-inspired concepts, and lab policy are distinguished in the [specification](docs/spec.md) and [verification notes](docs/workday-verification.md).

## Test locally

Install `uv`, Python 3.12, and PostgreSQL 16+ (Homebrew PostgreSQL 17 is supported), then run:

```sh
make test
```

The session fixture starts a separate temporary Postgres cluster, creates `mw_owner` and `mw_app`, applies `schema.sql`, and stops the cluster when pytest finishes. It does not touch an existing database or need Docker. Every test starts with fresh synthetic seed data. Test names carry the §11 acceptance IDs; parametrized cases use those IDs in their pytest case names.

The tests use real PostgreSQL permissions, forced row-level security, HTTP requests through both ASGI apps, and a clock that moves only through explicit set/advance calls. Audit-failure tests revoke database INSERT privileges and restore them in `finally` blocks.

## Run the containers

With Docker and the Compose plugin installed and the daemon running:

```sh
make up
# Optional disposable test-admin interface:
MW_TEST_ADMIN=1 make up

# Remove the containers and database volume:
make down
```

The image is tagged `mock-workday:m2`. Schema and seed are installed on the first startup of a fresh volume. `make down` removes that volume; the next `make up` reproduces the seed IDs. Signing keys and runtime-created records are regenerated.

Public API: `127.0.0.1:8080`. The tenant comes only from the HTTP Host header. No hosts-file change is needed:

```sh
curl -s http://127.0.0.1:8080/oauth2/token \
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

Other systems consume only the versioned HTTP contract and container image. There is no shared database or code interface.

## Documents

- [Implementation contract](docs/spec.md)
- [Public OpenAPI document](docs/openapi.json)
- [Original plan](docs/mock-workday-plan-claude.md)
- [Workday evidence and deliberate simplifications](docs/workday-verification.md)
