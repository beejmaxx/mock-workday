# Mock Workday

A small, multi-tenant HCM service modeled on public Workday concepts: supervisory organizations, Position Management, role-based constrained security, domain and business-process security policies, effective dating, and audit.

It exists as the protected enterprise system for the [Agent Cell Runtime](https://github.com/beejmaxx/agent-cell-runtime) learning project. It has no agent, cell, or execution concepts. It authenticates and authorizes every request itself, and other systems use it only through its published HTTP contract.

**Status:** design only. Nothing is implemented, and no guarantees have been demonstrated yet.

This is an independent learning project. It is not affiliated with or endorsed by Workday, and it does not reproduce Workday's implementation. Statements about Workday are labeled as facts (with sources), Workday-inspired choices, or lab policy. All data is synthetic.

## Documents

- [Proposed plan](docs/mock-workday-plan-claude.md): scope, security and identity model, the Change Job process, audit, milestones, and open decisions.
