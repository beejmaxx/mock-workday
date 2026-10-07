import json
import logging
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from .db import run
from .errors import APIError

logger = logging.getLogger(__name__)


def check_failure(conn):
    if conn.info.pop("audit_write_failure", False):
        raise APIError(503, "AUDIT_UNAVAILABLE")


def attribution(p):
    return {
        "by": p.actor_account_id or p.account_id,
        "behalf": p.account_id if p.asu_id and p.kind == "delegated" else None,
        "agent": p.agent_id,
        "asu": p.asu_id,
        "credential": p.credential_version_id,
        "legacy": p.kind == "delegated" and p.asu_id is None,
    }


def authz_record(conn, p, request_id, action, domain, target, decision, now):
    check_failure(conn)
    try:
        run(
            conn,
            """INSERT INTO audit_authz VALUES
            (:id,:tid,:rid,:aid,:cid,:gid,:action,:domain,:rtype,:resource,:decision,:reason,
             :group,:org,:job,:version,:now,:by,:behalf,:agent,:asu,:credential,:legacy)""",
            id=uuid4(),
            tid=p.tenant_id,
            rid=request_id,
            aid=p.account_id,
            cid=p.client_id,
            gid=p.grant_id,
            action=action,
            domain=domain,
            rtype=target.resource_type,
            resource=target.id,
            decision="ALLOW" if decision.allowed else "DENY",
            reason=decision.reason,
            group=decision.matched_group_id,
            org=decision.constraining_org_id,
            job=decision.job_revision_id,
            version=decision.policy_version,
            now=now,
            **attribution(p),
        )
    except SQLAlchemyError as exc:
        raise APIError(503, "AUDIT_UNAVAILABLE") from exc


def denial(db, p, request_id, action, domain, target, decision, now, *, fail=False):
    try:
        with db.tenant_tx(p.tenant_id) as conn:
            if fail:
                raise APIError(503, "AUDIT_UNAVAILABLE")
            authz_record(conn, p, request_id, action, domain, target, decision, now)
    except (SQLAlchemyError, APIError):
        logger.warning(
            json.dumps({"event": "denial_audit_unavailable", "request_id": request_id})
        )


def object_record(
    conn, p, request_id, object_type, object_id, field, old, new, now, bp_event_id=None
):
    check_failure(conn)
    try:
        run(
            conn,
            """INSERT INTO audit_objects VALUES
            (:id,:tid,:rid,:aid,:cid,:type,:oid,:field,CAST(:old AS jsonb),CAST(:new AS jsonb),:event,:now,:by,:behalf,:agent,:asu,:credential,:legacy)""",
            id=uuid4(),
            tid=p.tenant_id,
            rid=request_id,
            aid=p.account_id,
            cid=p.client_id,
            event=bp_event_id,
            type=object_type,
            oid=object_id,
            field=field,
            old=json.dumps(old, default=str),
            new=json.dumps(new, default=str),
            now=now,
            **attribution(p),
        )
    except SQLAlchemyError as exc:
        raise APIError(503, "AUDIT_UNAVAILABLE") from exc


def process_history(conn, p, event_id, action, step_key, comment, now):
    check_failure(conn)
    try:
        run(
            conn,
            """INSERT INTO bp_history VALUES
            (:id,:tid,:event,:action,:step,:aid,:cid,:comment,:now,:by,:behalf,:agent,:asu,:credential,:legacy)""",
            id=uuid4(),
            event=event_id,
            action=action,
            step=step_key,
            aid=p.account_id,
            cid=p.client_id,
            comment=comment,
            now=now,
            **attribution(p),
        )
    except SQLAlchemyError as exc:
        raise APIError(503, "AUDIT_UNAVAILABLE") from exc
