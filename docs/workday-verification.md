# Workday claim verification

**Date:** 2026-10-07. **Scope:** only the Workday-specific claims the detailed spec depends on. Primary sources are Workday's public documentation (`doc.workday.com`). Secondary sources are customer university guides and integration-vendor documentation; they show real configurations but are not Workday's own statements.

**Verdicts:**

- **Verified:** a primary source supports the claim.
- **Verified (secondary):** only customer or vendor documentation supports it.
- **Ambiguous:** sources partly support it or disagree.
- **Not publicly documented:** nothing found; the mock uses labeled lab policy.

## Summary

| # | Claim | Verdict | Effect on the mock |
|---|---|---|---|
| 1 | Org-scoped role access is configured on the security group, including an "unassigned subordinates" option | **Verified** | Model access rights per security group. Seed decision below. |
| 2 | A manager is not a member of the organization they manage | **Verified** | Keep as modeled |
| 3 | Role-based constrained groups limit access to the organizations where the role is assigned | **Verified** | Keep as modeled |
| 4a | Business-process security policies secure initiate, step actions (approve, deny), view, cancel, rescind, and correct | **Verified** | Keep as modeled |
| 4b | Cancel applies only while in progress; completed events are rescinded instead; statuses include In Progress, Successfully Completed, Denied, Canceled, Rescinded | **Verified (secondary)**; partly primary | Keep as modeled; rescind stays in the backlog |
| 4c | Taking part in a step exposes the event's details without general domain access to the worker | **Not publicly documented** | Keep as lab policy |
| 5 | Effective date is distinct from entry date; changes can be future-dated and take effect on that date; reports can view data "as of" a date | **Verified (secondary)**; partly primary | Keep as modeled |
| 6 | Integration system users, integration security groups (constrained and unconstrained), and OAuth API clients scoped to functional areas, with refresh tokens tied to an account | **Verified (secondary)**; partly primary | Keep as modeled; delegation stays lab policy |
| 7 | Business-process history and object audit trails are distinct | **Verified** | Keep three audit records; the authorization-decision audit is lab policy |
| 8 | REST responses reference objects as `{id, descriptor, href}` | **Verified (secondary)** | Keep. Pagination differs; see below. |
| — | Domain policies use View/Modify for tasks and reports, and Get/Put for integrations | **Verified** | Keep as modeled |

## Findings

### 1. Subordinate-organization access (decision required)

**Verified.** Access rights to organizations are configured on the role-based security group, not on the role assignment. Workday's administrator guide lists these options ([Workday](https://doc.workday.com/admin-guide/en-us/manage-workday/roles/dan1370796643079.html)):

- Current Organization and All Subordinates
- Current Organizations and Subordinates to Level
- Current Organization and Unassigned Subordinates Only

A customer guide also describes "Current Organization Only" ([UW–Madison](https://hr.wisc.edu/hr-guides/for-hr-professionals/guide-to-role-inheritance-and-access-rights-in-workday/)).

**Unassigned subordinates:** the role holder covers the current organization plus subordinate organizations where nobody holds the same assignable role. Workday's own example: Mary holds Absence Partner on the top organization and Xavier holds it on a subordinate organization. Mary can access every organization except Xavier's ([search excerpt of Workday documentation](https://doc.workday.com/admin-guide/en-us/manage-workday/roles/dan1370796643079.html)).

**Separate concept: role inheritance.** Role inheritance propagates role *assignments* to subordinate organizations. Access rights determine which organizations a holder can reach ([UW–Madison](https://hr.wisc.edu/hr-guides/for-hr-professionals/guide-to-role-inheritance-and-access-rights-in-workday/)). The mock models only access rights.

**Not publicly documented:** which option customers typically configure for the Manager group. It is tenant configuration.

**Lab policy (decided):**

- Access rights are a setting on each security group, with three values:
  - `CURRENT_ONLY`
  - `ALL_SUBORDINATES`
  - `UNASSIGNED_SUBORDINATES`
- "Subordinates to level" is not modeled.
- **Manager uses `ALL_SUBORDINATES`.** Alice sees Grace in Platform, even though Frank manages Platform.
- **HR Partner uses `UNASSIGNED_SUBORDINATES`.** A second HR Partner, Henry, is assigned on Platform. Carol therefore sees Bob but not Grace.

Using both values exercises both modes. It costs one enum and one seed worker, and it gives the runtime a non-obvious case in which the same organization tree yields different reach for different roles.

### 2. Manager membership

**Verified:** "A manager can't be a member of the organization they manage." Each supervisory organization has one superior and may have many subordinates ([Workday](https://doc.workday.com/admin-guide/en-us/manage-workday/organizations/manage-organization-concepts/concept--superior-and-subordinate-organizations.html)).

In the seed, Alice belongs to Executive, not to Engineering.

### 3. Role-based constrained security groups

**Verified:** role-based constrained groups identify support and leadership staff (managers, HR partners, and others) and constrain their access to the organizations where they hold role assignments ([Workday Education](https://doc.workday.com/workday-education/en-us/course-manuals/security-for-administrators/role-based-security.html); [Alight](https://alight.com/research-insights/understanding-workday-security)).

### 4. Business-process security

**Verified (4a):** each business process has a security policy. It controls who can:

- start the process
- perform action steps
- approve
- correct, cancel, and rescind
- reassign tasks
- view status ("View")

Deny and Ad Hoc Approve are also secured actions ([Workday](https://doc.workday.com/admin-guide/en-us/manage-workday/business-processes/business-process-framework-concepts/dan1370797847910.html); [Workday](https://doc.workday.com/admin-guide/en-us/manage-workday/business-processes/business-process-framework-concepts/mee1553714361673.html)).

**Verified (secondary) (4b):**

- Cancel stops an in-progress event, and no change goes into the system.
- A completed event cannot be canceled; it is rescinded, which rolls back its changes.
- The overall statuses observed are In Progress, Successfully Completed, Denied, Canceled, and Rescinded ([Texas A&M](https://it.tamus.edu/workdayservices/training/job_aid/correct-cancel-and-rescind/); [Workday](https://doc.workday.com/admin-guide/en-us/manage-workday/business-processes/manage-business-processes/ikj1637605088663.html)).
- These match the mock's statuses. Rescind remains in the backlog.

**Related (4c):** business-process policies can hide comments, details, or process history from the event's subject ("Hide Details from Person"), which shows that event visibility is controlled separately from domain data ([search excerpt of Workday documentation](https://doc.workday.com/admin-guide/en-us/manage-workday/business-processes/business-process-framework-concepts/dan1370797847910.html)).

**Not publicly documented (4c):** whether being assigned a step exposes event details without general domain access to the worker. The mock keeps its lab policy: step assignment grants visibility of that event and the fields the step needs, nothing more.

**Not modeled:** Send Back and Correct.

### 5. Effective dating

**Verified (secondary):**

- The effective date (when a change takes effect) is distinct from the entry date (when it was recorded).
- Effective dates may be past, present, or future.
- Customer guides describe a Change Job that cannot proceed when its effective date conflicts with an existing pending organization-assignment change ([Miami University](https://miamioh.teamdynamix.com/TDClient/1813/Portal/KB/ArticleDet?ID=168151); [UT Austin](https://workday.utexas.edu/news/change-organization-assignment-and-effective-dates)).

**Verified (primary):** reports can show data as of a chosen date ("View As Of") ([Workday](https://doc.workday.com/admin-guide/en-us/human-capital-management/staffing/basic-staffing-information/dan1370797452783.html)).

**Effect on the mock:**

- Keep effective-dated revisions and as-of reads.
- The one-pending-Change-Job rule is consistent with the conflict customers describe. The rule's exact form remains lab policy.
- Rejecting backdated changes is lab policy; Workday permits past effective dates.

### 6. Integration identity

**Verified (secondary):**

- Integration system users are non-human accounts, typically configured to disallow UI sessions.
- Integration system security groups come in unconstrained (all instances) and constrained (context-dependent subset) forms.
- OAuth 2.0 API clients are registered per tenant with scopes chosen from functional areas.
- Refresh tokens are generated for a specific Workday account, such as an integration system user.

Sources: [Glean](https://docs.glean.com/connectors/native/workday/setup), [Palo Alto Networks](https://cortex-docs.paloaltonetworks.com/cortex-xsiam/cloud-security/cortex-cloud-saas-security/onboard-a-supported-saas-application/onboard-workday), and [CData](https://cdn.cdata.com/help/JWN/ado/pg_isucreate.htm). Workday's education material describes the corresponding integration permissions ([Workday Education](https://doc.workday.com/workday-education/en-us/course-manuals/security-for-administrators/integrations.html)).

**Effect on the mock:**

- Keep integration system users, constrained and unconstrained integration groups, and API clients with scope ceilings.
- The delegation grant and token exchange remain lab policy.
- Workday's per-user refresh tokens are the closest public analogue to the grant, but this pass did not verify Workday's delegated-agent mechanics.

### 7. Audit and history

**Verified:** the two concepts are separate.

- **Business-process history:** for each step, when it was due, when it was completed, and by whom. It is viewable through the event or a worker's history, limited to the organizations the viewer supports ([Texas A&M](https://it.tamus.edu/workdayservices/training/job_aid/business-process-history/)).
- **Audit trails:** record "data added to or deleted from Workday, as well as how and when." Reports include View Audit Trail and View User or Task or Object Audit Trail, filtered by account, transaction, and business object ([Workday](https://doc.workday.com/admin-guide/en-us/manage-workday/tenant-configuration/auditing/wln1467042699742.html)).

**Not publicly documented:** per-read authorization-decision auditing. The mock's third audit record remains lab policy.

### 8. API reference shapes

**Verified (secondary):** REST responses represent objects and references as `{id, descriptor, href}`, with related objects such as the primary supervisory organization embedded as references ([Workato](https://docs.workato.com/connectors/workday-rest/get-worker-action); [Apideck](https://www.apideck.com/blog/guide-to-build-workday-hris-api-integrations)).

**Divergence noted:** the same secondary sources show offset-based pagination (`offset`, `limit`, `total`), not keyset pagination.

The mock keeps keyset pagination as a labeled lab policy. Its guarantee is easier to state, and offset pagination's duplicates and gaps under concurrent change are something the runtime should be built to tolerate anyway. An offset mode could be added later to test that tolerance.

### Domain permissions

**Verified:** domain security policies grant View or View and Modify for tasks and reports, and Get or Get and Put for integrations. Domains group securable actions, reporting items, and integrations (web-service operations) ([Workday Education](https://doc.workday.com/workday-education/en-us/course-manuals/security-for-administrators/integrations.html)).

## What this pass did not check

- Workday's delegated-agent mechanics (Agent System of Record, agent system users) beyond public announcements.
- Field-level security inside a single domain.
- Exact business-process status transitions for Send Back and Correct.

None of these block the detailed spec.
