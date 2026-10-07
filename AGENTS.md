# Project instructions

- Current phase: **MW-M3, Mock Workday as a fuller "Workday core" for the agent runtime to integrate with** (MW-M1, MW-M2, MW-D1 are done). Checkpoints, each ending with a commit and a stop for review (do not push):
  1. **Spec draft only:** write `docs/m3-spec.md` (scope below). No code.
  2. **Code and tests**, each slice committed and stopped for review: **2a** storage/ABAC/local branch, bulk seed, balances; **2b** agent identity/credentials/audit/logs; **2c** exports/events/outbox; **2d** native AI/fake/usage/EMF. Test IDs as in `docs/spec.md` §11.
  3. **Terraform and scripts:** extend `infra/` with the approved resources; run only `fmt`, `validate`, `plan`. Stop and report the plan.
  4. **Only after the reviewer approves the plan:** apply, smoke test, record results, then `make aws-down` and a clean `make aws-leftovers`. Nothing stays up between sessions.
- MW-M3 AWS limits: create only resources the approved spec describes; never modify or destroy the bootstrap or foundation state or the state bucket (platform repository, `~/code/aws`); read network IDs only through the SSM parameters. Never run apply before checkpoint 4 approval.
- `docs/spec.md` is the contract. If it is ambiguous, contradictory, or seems wrong, ask instead of inventing semantics. Record any agreed change in the spec in the same commit as the code.
- Mock Workday must contain no agent-runtime concepts: no executions, cells, sandboxes, or runtime lifecycle. **Narrowed for MW-M3:** Workday's own published agent-identity model (agent registration, Agent System Users, their OAuth clients and credentials, delegate/ambient authentication, agent audit attribution) is Workday-core behavior and may be modeled, labeled with primary sources. Otherwise it remains an ordinary multi-tenant enterprise service.
- Its primary interface to other systems is its versioned HTTP contract (OpenAPI document plus container image), with approved versioned EventBridge notifications and API-authorized short-lived report URLs as explicit exceptions. Never design for shared code or database access with the runtime.
- Label claims as Workday fact (cite a primary source), Workday-inspired, or lab policy. Do not present lab policy as Workday behavior.
- Use synthetic data only. Keep credentials and private interview context out of version control. Seed passwords are synthetic and documented in the spec.

## Keep it simple

This is a learning lab. Small, readable, direct code beats flexibility. The spec deliberately fixes most choices; do not reopen them.

- Use the stack in spec §0: synchronous FastAPI handlers, SQLAlchemy 2 Core (no ORM models), psycopg 3, PyJWT, pytest, plain `schema.sql`. Do not add async, Alembic, Redis, Celery, OpenTelemetry, dependency-injection frameworks, or plugin systems.
- Use plain functions and small dataclasses. Add an abstraction (base class, protocol, registry, strategy, factory) only when two concrete call sites need it today.
- The authorization logic is the pseudocode in spec §4, written directly. No policy DSL, rules engine, or generic permission framework.
- The business-process code in M2 handles exactly Change Job and Request Time Off. Do not build a general workflow engine.
- No configuration options beyond those the spec names. No feature flags except `MW_TEST_ADMIN`.
- Prefer one obvious module per concern (see the layout in spec §0). Avoid deep package hierarchies and files that only re-export.
- Every behavior in the spec gets a test from the §11 matrix, using the test IDs in test names. Do not add speculative tests for behavior the spec does not define.
- Comments explain why, not what. No docstrings that restate the function name.

## Commits

- Small, focused commits with clear messages.
- Do not add AI attribution or co-author trailers to commits or pull requests.
