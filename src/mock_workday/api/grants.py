from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response

from ..audit import object_record
from ..authz import Target
from ..db import one, rows, run
from ..errors import APIError
from .models import Grant, GrantInput

router = APIRouter(prefix="/api/v1/delegation-grants")


def shape(row):
    return {
        k: row[k]
        for k in ("client_id", "scopes", "created_at", "expires_at", "revoked_at")
    } | {"id": row["id"].hex}


@router.post("", status_code=201, response_model=Grant)
def create(request: Request, body: GrantInput):
    with request.app.state.service.request(request) as ctx:
        gid = uuid4()
        ctx.grant_access("WRITE", gid)
        client = one(
            ctx.conn,
            "SELECT * FROM api_clients WHERE tenant_id=:tid AND client_id=:id AND NOT disabled",
            id=body.client_id,
        )
        if client and client["asu_id"]:
            asu = one(
                ctx.conn,
                "SELECT mode FROM agent_system_users WHERE tenant_id=:tid AND id=:id",
                id=client["asu_id"],
            )
            if asu["mode"] != "DELEGATE":
                raise APIError(422, "VALIDATION_ERROR")
        if not client or not set(body.scopes) <= set(client["scope_ceiling"]):
            raise APIError(422, "VALIDATION_ERROR")
        grant = one(
            ctx.conn,
            """INSERT INTO delegation_grants
            (id,tenant_id,user_account_id,client_id,scopes,created_at,expires_at)
            VALUES (:id,:tid,:aid,:cid,:scopes,:now,:expires) RETURNING *""",
            id=gid,
            aid=ctx.p.account_id,
            cid=body.client_id,
            scopes=sorted(set(body.scopes)),
            now=ctx.now,
            expires=ctx.now + timedelta(seconds=body.ttl_seconds),
        )
        result = shape(grant)
        object_record(
            ctx.conn,
            ctx.p,
            ctx.request_id,
            "delegation_grants",
            gid,
            "created",
            None,
            result,
            ctx.now,
        )
        return result


@router.get("", response_model=list[Grant])
def list_grants(request: Request):
    with request.app.state.service.request(request) as ctx:
        ctx.grant_access("READ")
        return [
            shape(row)
            for row in rows(
                ctx.conn,
                "SELECT * FROM delegation_grants WHERE tenant_id=:tid AND user_account_id=:id ORDER BY id",
                id=ctx.p.account_id,
            )
        ]


@router.delete("/{wid}", status_code=204)
def revoke(request: Request, wid: UUID):
    with request.app.state.service.request(request) as ctx:
        ctx.grant_access("WRITE", wid)
        grant = one(
            ctx.conn,
            "SELECT * FROM delegation_grants WHERE tenant_id=:tid AND id=:id AND user_account_id=:aid FOR UPDATE",
            id=wid,
            aid=ctx.p.account_id,
        )
        if not grant:
            ctx.not_found(Target("delegation-grants", wid), action="WRITE")
        if grant["revoked_at"] is None:
            run(
                ctx.conn,
                "UPDATE delegation_grants SET revoked_at=:now WHERE tenant_id=:tid AND id=:id",
                now=ctx.now,
                id=wid,
            )
            object_record(
                ctx.conn,
                ctx.p,
                ctx.request_id,
                "delegation_grants",
                wid,
                "revoked_at",
                None,
                ctx.now,
                ctx.now,
            )
        return Response(status_code=204, headers={"X-Request-Id": ctx.request_id})
