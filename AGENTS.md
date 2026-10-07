# Project instructions

- Current phase: implement **M1** exactly as specified in `docs/spec.md`. Then stop and report for review before starting M2.
- No Terraform or AWS work in M1. AWS deployment is milestone D1 (spec §12), which gets its own spec first.
- `docs/spec.md` is the contract. If it is ambiguous, contradictory, or seems wrong, ask instead of inventing semantics. Record any agreed change in the spec in the same commit as the code.
- Mock Workday must contain no agent, cell, or execution concepts. It is an ordinary multi-tenant enterprise service.
- Its only interface to other systems is its versioned HTTP contract (OpenAPI document plus container image). Never design for shared code or database access with the runtime.
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
