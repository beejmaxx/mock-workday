import json
import logging
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from .db import run
from .errors import APIError

logger = logging.getLogger(__name__)


def authz_record(conn, p, request_id, action, domain, target, decision, now):
    try:
        run(
            conn,
            """INSERT INTO audit_authz VALUES
            (:id,:tid,:rid,:aid,:cid,:gid,:action,:domain,:rtype,:resource,:decision,:reason,
             :group,:org,:job,:version,:now)""",
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
        )
    except SQLAlchemyError as exc:
        raise APIError(503, "AUDIT_UNAVAILABLE") from exc


def denial(db, p, request_id, action, domain, target, decision, now):
    try:
        with db.tenant_tx(p.tenant_id) as conn:
            authz_record(conn, p, request_id, action, domain, target, decision, now)
    except (SQLAlchemyError, APIError):
        logger.warning("Denial audit unavailable for request %s", request_id)


def object_record(conn, p, request_id, object_type, object_id, field, old, new, now):
    try:
        run(
            conn,
            """INSERT INTO audit_objects VALUES
            (:id,:tid,:rid,:aid,:cid,:type,:oid,:field,CAST(:old AS jsonb),CAST(:new AS jsonb),NULL,:now)""",
            id=uuid4(),
            tid=p.tenant_id,
            rid=request_id,
            aid=p.account_id,
            cid=p.client_id,
            type=object_type,
            oid=object_id,
            field=field,
            old=json.dumps(old, default=str),
            new=json.dumps(new, default=str),
            now=now,
        )
    except SQLAlchemyError as exc:
        raise APIError(503, "AUDIT_UNAVAILABLE") from exc
