from dataclasses import dataclass
from uuid import UUID

from .db import one, rows


@dataclass(frozen=True)
class Target:
    resource_type: str
    id: UUID | None
    worker_id: UUID | None = None
    org_id: UUID | None = None
    job_revision_id: UUID | None = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    matched_group_id: UUID | None
    constraining_org_id: UUID | None
    policy_version: int
    job_revision_id: UUID | None


def job_as_of(conn, worker_id, day):
    return one(
        conn,
        """SELECT j.*, p.org_id, p.title, p.ref_id AS position_ref
        FROM job_revisions j JOIN positions p ON p.id=j.position_id AND p.tenant_id=:tid
        WHERE j.tenant_id=:tid AND j.worker_id=:id AND j.effective_date<=:day
        ORDER BY j.effective_date DESC, j.recorded_seq DESC LIMIT 1""",
        id=worker_id,
        day=day,
    )


def worker_target(conn, wid, now, *, resource_type="workers", resource_id=None):
    job = job_as_of(conn, wid, now.date())
    return Target(
        resource_type,
        resource_id or wid,
        wid,
        job["org_id"] if job else None,
        job["id"] if job else None,
    )


def descendants(conn, org_id):
    return {
        r["id"]
        for r in rows(
            conn,
            """WITH RECURSIVE tree AS (
        SELECT id FROM organizations WHERE tenant_id=:tid AND superior_id=:id
        UNION SELECT o.id FROM organizations o JOIN tree t ON o.superior_id=t.id WHERE o.tenant_id=:tid)
        SELECT id FROM tree""",
            id=org_id,
        )
    }


def filled_assignments(conn, now):
    return rows(
        conn,
        """WITH current_jobs AS (
        SELECT DISTINCT ON (worker_id) worker_id, position_id FROM job_revisions
        WHERE tenant_id=:tid AND effective_date<=:day ORDER BY worker_id, effective_date DESC, recorded_seq DESC)
        SELECT ra.*, j.worker_id FROM role_assignments ra
        JOIN current_jobs j ON j.position_id=ra.position_id
        JOIN workers w ON w.id=j.worker_id AND w.tenant_id=:tid AND w.active
        WHERE ra.tenant_id=:tid AND ra.revoked_at IS NULL ORDER BY ra.id""",
        day=now.date(),
    )


def reach(conn, role, anchor_org, access_rights, now):
    if access_rights == "CURRENT_ONLY":
        return {anchor_org}
    if access_rights == "ALL_SUBORDINATES":
        return {anchor_org} | descendants(conn, anchor_org)
    children = {}
    for org in rows(
        conn,
        "SELECT id, superior_id FROM organizations WHERE tenant_id=:tid ORDER BY id",
    ):
        children.setdefault(org["superior_id"], []).append(org["id"])
    assigned = {r["org_id"] for r in filled_assignments(conn, now) if r["role"] == role}
    out, stack = {anchor_org}, list(children.get(anchor_org, []))
    while stack:
        org = stack.pop()
        if org in assigned:
            continue
        out.add(org)
        stack.extend(children.get(org, []))
    return out


def memberships(conn, p, target, now):
    result = []
    if p.kind in ("human", "delegated"):
        worker = one(
            conn,
            "SELECT * FROM workers WHERE tenant_id=:tid AND id=:id AND active",
            id=p.worker_id,
        )
        if not worker:
            return result
        for group in rows(
            conn,
            "SELECT * FROM security_groups WHERE tenant_id=:tid AND kind IN ('ALL_EMPLOYEES','SELF') ORDER BY kind, id",
        ):
            if group["kind"] == "ALL_EMPLOYEES" or target.worker_id == p.worker_id:
                result.append((group["id"], None))
        job = job_as_of(conn, p.worker_id, now.date())
        if job and target.org_id:
            for ra in rows(
                conn,
                """SELECT ra.*, g.id AS group_id, g.access_rights FROM role_assignments ra
                    JOIN security_groups g ON g.role=ra.role AND g.tenant_id=:tid AND g.kind='ROLE_BASED'
                    WHERE ra.tenant_id=:tid AND ra.position_id=:pos AND ra.revoked_at IS NULL ORDER BY g.id, ra.id""",
                pos=job["position_id"],
            ):
                if target.org_id in reach(
                    conn, ra["role"], ra["org_id"], ra["access_rights"], now
                ):
                    result.append((ra["group_id"], ra["org_id"]))
    else:
        for group in rows(
            conn,
            """SELECT g.* FROM security_groups g JOIN integration_group_members m ON m.group_id=g.id AND m.tenant_id=:tid
                WHERE g.tenant_id=:tid AND m.account_id=:id ORDER BY g.id""",
            id=p.account_id,
        ):
            if group["kind"] == "INTEGRATION_UNCONSTRAINED":
                result.append((group["id"], None))
            elif group["kind"] == "INTEGRATION_CONSTRAINED" and target.org_id:
                for anchor in rows(
                    conn,
                    "SELECT org_id FROM integration_group_orgs WHERE tenant_id=:tid AND group_id=:id ORDER BY org_id",
                    id=group["id"],
                ):
                    if target.org_id in {anchor["org_id"]} | descendants(
                        conn, anchor["org_id"]
                    ):
                        result.append((group["id"], anchor["org_id"]))
    return result


def authorize(conn, p, action, domain, target, now):
    version = one(
        conn, "SELECT policy_version FROM tenant_config WHERE tenant_id=:tid"
    )["policy_version"]

    def decision(allowed, reason, group=None, org=None):
        return Decision(allowed, reason, group, org, version, target.job_revision_id)

    scope = (
        "documents"
        if domain.startswith("DOC_")
        else {
            "WORKER_BASIC": "staffing",
            "WORKER_ORGANIZATIONS": "staffing",
            "WORKER_COMPENSATION": "compensation",
            "ABSENCE": "absence",
        }[domain]
    )
    if p.scopes is not None and scope not in p.scopes:
        return decision(False, "SCOPE")
    for group, org in memberships(conn, p, target, now):
        grant = one(
            conn,
            "SELECT permission FROM domain_grants WHERE tenant_id=:tid AND domain=:domain AND group_id=:gid",
            domain=domain,
            gid=group,
        )
        if grant and (action == "READ" or grant["permission"] == "MODIFY"):
            return decision(True, "GRANT", group, org)
    return decision(False, "NO_GRANT")
