# Project instructions

- Current phase: verify the Workday claims the plan depends on, resolve the remaining decisions, and write the detailed spec and test matrix. Do not implement application code until the user approves the spec.
- Mock Workday must contain no agent, cell, or execution concepts. It is an ordinary multi-tenant enterprise service.
- Its only interface to other systems is its versioned HTTP contract (OpenAPI document plus container image). Never design for shared code or database access with the runtime.
- Label claims as Workday fact (cite a primary source), Workday-inspired, or lab policy. Do not present lab policy as Workday behavior.
- Build only what the current milestone requires. In particular, do not grow the business-process model into a general-purpose workflow engine.
- Use synthetic data only. Keep credentials and private interview context out of version control.
