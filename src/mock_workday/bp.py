import copy
import json
from uuid import UUID, uuid4

from . import audit
from .authz import (
    Decision,
    Target,
    authorize,
    filled_assignments,
    job_as_of,
    memberships,
    reach,
    worker_target,
)
from .db import advisory_lock, one, rows, run
from .errors import APIError


def event_row(ctx, eid, *, lock=False):
    return one(
        ctx.conn,
        "SELECT * FROM bp_events WHERE tenant_id=:tid AND id=:id"
        + (" FOR UPDATE" if lock else ""),
        id=eid,
    )


def event_steps(ctx, eid):
    return rows(
        ctx.conn,
        "SELECT * FROM bp_steps WHERE tenant_id=:tid AND event_id=:id ORDER BY step_order",
        id=eid,
    )


def process_domain(event):
    return "WORKER_ORGANIZATIONS" if event["type"] == "CHANGE_JOB" else "ABSENCE"


def has_scopes(p, *scopes):
    return p.scopes is None or set(scopes) <= p.scopes


def process_scopes(event):
    if event["type"] == "REQUEST_TIME_OFF":
        return ("absence",)
    return (
        ("staffing", "compensation")
        if event["payload"].get("compensation")
        else ("staffing",)
    )


def routing_org(ctx, event):
    if event["type"] == "CHANGE_JOB":
        return one(
            ctx.conn,
            "SELECT org_id FROM positions WHERE tenant_id=:tid AND id=:id",
            id=UUID(event["payload"]["position_id"]),
        )["org_id"]
    job = job_as_of(ctx.conn, event["subject_worker_id"], ctx.now.date())
    return job["org_id"] if job else None


def assignees(ctx, event, key):
    org = routing_org(ctx, event)
    if org is None:
        return []
    accounts = {
        a["worker_id"]: a["id"]
        for a in rows(
            ctx.conn,
            "SELECT id,worker_id FROM accounts WHERE tenant_id=:tid AND kind='HUMAN' AND NOT disabled ORDER BY id",
        )
    }
    assignments = filled_assignments(ctx.conn, ctx.now)
    if key == "COMPENSATION_PARTNER":
        group = one(
            ctx.conn,
            "SELECT access_rights FROM security_groups WHERE tenant_id=:tid AND role='COMPENSATION_PARTNER'",
        )
        return sorted(
            {
                accounts[r["worker_id"]]
                for r in assignments
                if r["role"] == "COMPENSATION_PARTNER"
                and r["worker_id"] in accounts
                and org
                in reach(
                    ctx.conn, r["role"], r["org_id"], group["access_rights"], ctx.now
                )
            }
        )
    while org:
        candidates = {
            accounts[r["worker_id"]]
            for r in assignments
            if r["role"] == "MANAGER"
            and r["org_id"] == org
            and r["worker_id"] in accounts
            and r["worker_id"] != event["subject_worker_id"]
            and accounts[r["worker_id"]] != event["initiator_account_id"]
        }
        if candidates:
            return sorted(candidates)
        org = one(
            ctx.conn,
            "SELECT superior_id FROM organizations WHERE tenant_id=:tid AND id=:id",
            id=org,
        )["superior_id"]
    return []


def current_assignees(ctx, event, step):
    if event["status"] != "IN_PROGRESS" or step["status"] not in (
        "PENDING",
        "AWAITING",
    ):
        return []
    return assignees(ctx, event, step["step_key"])


def can_view(ctx, event, steps):
    scope = "staffing" if event["type"] == "CHANGE_JOB" else "absence"
    if not has_scopes(ctx.p, scope):
        return False
    if (
        ctx.p.account_id == event["initiator_account_id"]
        or ctx.p.worker_id == event["subject_worker_id"]
    ):
        return True
    for step in steps:
        if (
            ctx.p.account_id in step["initial_assignee_account_ids"]
            or step["acted_by"] == ctx.p.account_id
            or ctx.p.account_id in current_assignees(ctx, event, step)
        ):
            return True
    target = worker_target(ctx.conn, event["subject_worker_id"], ctx.now)
    return authorize(
        ctx.conn, ctx.p, "READ", process_domain(event), target, ctx.now
    ).allowed


def compensation_visible(ctx, event, steps):
    if not has_scopes(ctx.p, "compensation"):
        return False
    target = worker_target(ctx.conn, event["subject_worker_id"], ctx.now)
    if authorize(
        ctx.conn, ctx.p, "READ", "WORKER_COMPENSATION", target, ctx.now
    ).allowed:
        return True
    return any(
        s["step_key"] == "COMPENSATION_PARTNER"
        and s["status"] == "AWAITING"
        and ctx.p.account_id in current_assignees(ctx, event, s)
        for s in steps
    )


def decision(
    ctx,
    event,
    allowed,
    reason,
    *,
    action="WRITE",
    domain=None,
    sensitive=False,
    group=None,
    org=None,
):
    target = worker_target(
        ctx.conn,
        event["subject_worker_id"],
        ctx.now,
        resource_type="business-process-events",
        resource_id=event["id"],
    )
    version = one(
        ctx.conn, "SELECT policy_version FROM tenant_config WHERE tenant_id=:tid"
    )["policy_version"]
    value = Decision(allowed, reason, group, org, version, target.job_revision_id)
    ctx.record_decision(
        action, domain or process_domain(event), target, value, sensitive=sensitive
    )
    return allowed


def require_view(ctx, event, steps):
    if not can_view(ctx, event, steps):
        decision(ctx, event, False, "EVENT_VISIBILITY", action="READ")
        raise APIError(404, "NOT_FOUND")


def reference(event):
    return {
        "id": event["id"].hex,
        "descriptor": event["type"].replace("_", " ").title(),
        "href": f"/api/v1/business-process-events/{event['id'].hex}",
    }


def event_shape(ctx, event, steps):
    result = {
        k: event[k]
        for k in (
            "type",
            "status",
            "current_step",
            "effective_date",
            "payload",
            "comment",
            "version",
            "initiated_at",
            "completed_at",
        )
    }
    result.update(reference(event))
    result.update(
        subject_worker_id=event["subject_worker_id"].hex,
        initiator_account_id=event["initiator_account_id"].hex,
        initiator_client_id=event["initiator_client_id"],
    )
    result["steps"] = [
        {
            "id": s["id"].hex,
            "step_order": s["step_order"],
            "step_key": s["step_key"],
            "status": s["status"],
            "initial_assignee_account_ids": [
                a.hex for a in s["initial_assignee_account_ids"]
            ],
            "current_assignee_account_ids": [
                a.hex for a in current_assignees(ctx, event, s)
            ],
            "acted_by": s["acted_by"].hex if s["acted_by"] else None,
            "acted_by_client": s["acted_by_client"],
            "acted_at": s["acted_at"],
            "comment": s["comment"],
        }
        for s in steps
    ]
    return disclose(ctx, event, steps, result)


def disclose(ctx, event, steps, stored):
    result = copy.deepcopy(stored)
    permitted = compensation_visible(ctx, event, steps)
    if not permitted:
        result["payload"].pop("compensation", None)
        for step in result["steps"]:
            if step["step_key"] == "COMPENSATION_PARTNER":
                step.pop("comment", None)
    elif result["payload"].get("compensation") is not None or any(
        s["step_key"] == "COMPENSATION_PARTNER" and s.get("comment")
        for s in result["steps"]
    ):
        decision(
            ctx,
            event,
            True,
            "COMPENSATION_DISCLOSURE",
            action="READ",
            domain="WORKER_COMPENSATION",
            sensitive=True,
        )
    return result


def lock_worker(ctx, wid):
    advisory_lock(ctx.conn, f"worker:{ctx.p.tenant_id}:{wid}")
    ctx.now = ctx.service.clock.now()
    worker = one(
        ctx.conn, "SELECT * FROM workers WHERE tenant_id=:tid AND id=:id", id=wid
    )
    if not worker or not job_as_of(ctx.conn, wid, ctx.now.date()):
        ctx.not_found(Target("workers", wid), action="WRITE")
    return worker


def position_available(ctx, pid, day, subject, exclude=None):
    occupied = one(
        ctx.conn,
        """WITH current_jobs AS (
        SELECT DISTINCT ON (worker_id) worker_id,position_id FROM job_revisions
        WHERE tenant_id=:tid AND effective_date<=:day ORDER BY worker_id,effective_date DESC,recorded_seq DESC)
        SELECT worker_id FROM current_jobs WHERE position_id=:pid AND worker_id<>:subject LIMIT 1""",
        day=day,
        pid=pid,
        subject=subject,
    )
    if occupied:
        raise APIError(409, "POSITION_OCCUPIED")
    pending = one(
        ctx.conn,
        """SELECT subject_worker_id FROM bp_events WHERE tenant_id=:tid AND type='CHANGE_JOB'
        AND (status='IN_PROGRESS' OR (status='SUCCESSFULLY_COMPLETED' AND effective_date>:today))
        AND payload->>'position_id'=:pid AND (CAST(:exclude AS uuid) IS NULL OR id<>CAST(:exclude AS uuid)) LIMIT 1""",
        today=ctx.now.date(),
        pid=pid.hex,
        exclude=str(exclude) if exclude else None,
    )
    if pending:
        raise APIError(
            409,
            "PENDING_CHANGE_EXISTS"
            if pending["subject_worker_id"] == subject
            else "POSITION_OCCUPIED",
        )


def initiate(ctx, kind, body):
    lock_worker(ctx, body.worker_id)
    payload = body.model_dump(mode="json")
    payload["worker_id"] = body.worker_id.hex
    if kind == "CHANGE_JOB":
        payload["position_id"] = body.position_id.hex
    event = {
        "id": uuid4(),
        "type": kind,
        "subject_worker_id": body.worker_id,
        "initiator_account_id": ctx.p.account_id,
        "initiator_client_id": ctx.p.client_id,
        "status": "IN_PROGRESS",
        "current_step": 1,
        "effective_date": body.effective_date if kind == "CHANGE_JOB" else None,
        "payload": payload,
        "comment": body.comment if kind == "CHANGE_JOB" else "",
        "version": 1,
        "initiated_at": ctx.now,
        "completed_at": None,
    }
    target = worker_target(ctx.conn, body.worker_id, ctx.now)
    if kind == "CHANGE_JOB":
        permitted = False
        match_group = match_org = None
        if has_scopes(ctx.p, *process_scopes(event)):
            for group, org in memberships(ctx.conn, ctx.p, target, ctx.now):
                g = one(
                    ctx.conn,
                    "SELECT role FROM security_groups WHERE tenant_id=:tid AND id=:id",
                    id=group,
                )
                if g["role"] in ("MANAGER", "HR_PARTNER"):
                    permitted, match_group, match_org = True, group, org
                    break
        if not decision(
            ctx, event, permitted, "BP_INITIATION", group=match_group, org=match_org
        ):
            raise APIError(403, "FORBIDDEN")
        if body.effective_date < ctx.now.date():
            raise APIError(422, "VALIDATION_ERROR")
        position = one(
            ctx.conn,
            "SELECT * FROM positions WHERE tenant_id=:tid AND id=:id",
            id=body.position_id,
        )
        if (
            not position
            or body.position_id
            == job_as_of(ctx.conn, body.worker_id, ctx.now.date())["position_id"]
        ):
            raise APIError(422, "VALIDATION_ERROR")
        advisory_lock(ctx.conn, f"position:{ctx.p.tenant_id}:{body.position_id}")
        position_available(ctx, body.position_id, body.effective_date, body.worker_id)
        pending = one(
            ctx.conn,
            """SELECT id FROM bp_events WHERE tenant_id=:tid AND subject_worker_id=:wid
            AND type='CHANGE_JOB' AND (status='IN_PROGRESS' OR (status='SUCCESSFULLY_COMPLETED' AND effective_date>:today)) LIMIT 1""",
            wid=body.worker_id,
            today=ctx.now.date(),
        )
        if pending:
            raise APIError(409, "PENDING_CHANGE_EXISTS")
        step_defs = [
            ("RECEIVING_MANAGER", "AWAITING"),
            ("COMPENSATION_PARTNER", "PENDING" if body.compensation else "SKIPPED"),
        ]
    else:
        permitted = (
            ctx.p.worker_id == body.worker_id
            and authorize(ctx.conn, ctx.p, "WRITE", "ABSENCE", target, ctx.now).allowed
        )
        if not decision(ctx, event, permitted, "SELF_INITIATION"):
            raise APIError(403, "FORBIDDEN")
        if body.start_date > body.end_date:
            raise APIError(422, "VALIDATION_ERROR")
        step_defs = [("MANAGER_APPROVAL", "AWAITING")]
    run(
        ctx.conn,
        """INSERT INTO bp_events VALUES
        (:id,:tid,:type,:subject_worker_id,:initiator_account_id,:initiator_client_id,:status,:current_step,
         :effective_date,CAST(:payload AS jsonb),:comment,:version,:initiated_at,:completed_at)""",
        **(event | {"payload": json.dumps(payload)}),
    )
    for order, (key, status) in enumerate(step_defs, 1):
        run(
            ctx.conn,
            """INSERT INTO bp_steps
            (id,tenant_id,event_id,step_order,step_key,status,initial_assignee_account_ids)
            VALUES (:id,:tid,:event,:order,:key,:status,:assignees)""",
            id=uuid4(),
            event=event["id"],
            order=order,
            key=key,
            status=status,
            assignees=assignees(ctx, event, key) if status != "SKIPPED" else [],
        )
    audit.process_history(
        ctx.conn, ctx.p, event["id"], "INITIATE", None, event["comment"], ctx.now
    )
    audit.object_record(
        ctx.conn,
        ctx.p,
        ctx.request_id,
        "bp_events",
        event["id"],
        "status",
        None,
        "IN_PROGRESS",
        ctx.now,
        event["id"],
    )
    return event


def insert_revisions(ctx, event):
    payload = event["payload"]
    pid = UUID(payload["position_id"])
    advisory_lock(ctx.conn, f"position:{ctx.p.tenant_id}:{pid}")
    position_available(
        ctx,
        pid,
        event["effective_date"],
        event["subject_worker_id"],
        exclude=event["id"],
    )
    for table, values in [
        ("job_revisions", {"position_id": pid}),
        *(
            [("compensation_revisions", payload["compensation"])]
            if payload.get("compensation")
            else []
        ),
    ]:
        revision_id = uuid4()
        run(
            ctx.conn,
            f"""INSERT INTO {table} (id,tenant_id,worker_id,effective_date,recorded_at,bp_event_id,{",".join(values)})
            VALUES (:id,:tid,:worker,:day,:now,:event,{",".join(":" + k for k in values)})""",
            id=revision_id,
            worker=event["subject_worker_id"],
            day=event["effective_date"],
            now=ctx.now,
            event=event["id"],
            **values,
        )
        audit.object_record(
            ctx.conn,
            ctx.p,
            ctx.request_id,
            table,
            revision_id,
            "created",
            None,
            values,
            ctx.now,
            event["id"],
        )


def act(ctx, eid, action, body):
    event = event_row(ctx, eid, lock=True)
    if not event:
        ctx.not_found(Target("business-process-events", eid), action="WRITE")
    lock_worker(ctx, event["subject_worker_id"])
    steps = event_steps(ctx, eid)
    require_view(ctx, event, steps)
    if event["status"] != "IN_PROGRESS":
        raise APIError(409, "INVALID_STATE")
    current = next(s for s in steps if s["step_order"] == event["current_step"])
    if (
        body.expected_step != current["step_key"]
        or body.expected_version != event["version"]
    ):
        raise APIError(409, "VERSION_CONFLICT")
    if action == "cancel":
        permitted = ctx.p.account_id == event["initiator_account_id"]
    else:
        permitted = (
            ctx.p.worker_id != event["subject_worker_id"]
            and (
                action != "approve" or ctx.p.account_id != event["initiator_account_id"]
            )
            and ctx.p.account_id in assignees(ctx, event, current["step_key"])
        )
    permitted = permitted and has_scopes(ctx.p, *process_scopes(event))
    if not decision(ctx, event, permitted, "BP_ACTOR"):
        raise APIError(403, "FORBIDDEN")
    if (
        action == "approve"
        and event["type"] == "CHANGE_JOB"
        and event["effective_date"] < ctx.now.date()
    ):
        raise APIError(409, "EFFECTIVE_DATE_PASSED")
    following = next(
        (
            s
            for s in steps
            if s["step_order"] > current["step_order"] and s["status"] == "PENDING"
        ),
        None,
    )
    status = (
        "CANCELED"
        if action == "cancel"
        else "DENIED"
        if action == "deny"
        else "IN_PROGRESS"
        if following
        else "SUCCESSFULLY_COMPLETED"
    )
    if status == "SUCCESSFULLY_COMPLETED" and event["type"] == "CHANGE_JOB":
        insert_revisions(ctx, event)
    run(
        ctx.conn,
        """UPDATE bp_steps SET status=:status,acted_by=:aid,acted_by_client=:cid,acted_at=:now,comment=:comment
        WHERE tenant_id=:tid AND id=:id""",
        id=current["id"],
        status={"approve": "APPROVED", "deny": "DENIED", "cancel": "CANCELED"}[action],
        aid=ctx.p.account_id,
        cid=ctx.p.client_id,
        now=ctx.now,
        comment=body.comment,
    )
    if status == "IN_PROGRESS":
        run(
            ctx.conn,
            "UPDATE bp_steps SET status='AWAITING' WHERE tenant_id=:tid AND id=:id",
            id=following["id"],
        )
    else:
        run(
            ctx.conn,
            "UPDATE bp_steps SET status='CANCELED' WHERE tenant_id=:tid AND event_id=:id AND status IN ('PENDING','AWAITING')",
            id=eid,
        )
    run(
        ctx.conn,
        """UPDATE bp_events SET status=:status,current_step=:step,version=version+1,completed_at=:completed
        WHERE tenant_id=:tid AND id=:id""",
        id=eid,
        status=status,
        step=following["step_order"] if status == "IN_PROGRESS" else None,
        completed=ctx.now if status != "IN_PROGRESS" else None,
    )
    audit.process_history(
        ctx.conn, ctx.p, eid, action.upper(), current["step_key"], body.comment, ctx.now
    )
    audit.object_record(
        ctx.conn,
        ctx.p,
        ctx.request_id,
        "bp_events",
        eid,
        "status",
        event["status"],
        status,
        ctx.now,
        eid,
    )
    return event_row(ctx, eid)
