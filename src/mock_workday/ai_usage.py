import json
from datetime import timedelta
from uuid import uuid4

from . import audit, metrics
from .db import advisory_lock, one, rows, run
from .errors import APIError

INPUT_RESERVATION = 131072
OUTPUT_RESERVATION = 512


def record(conn, invocation_id, phase, now, **metadata):
    audit.check_failure(conn)
    run(
        conn,
        """INSERT INTO audit_ai VALUES
        (:id,:tid,:invocation,:phase,:now,CAST(:metadata AS jsonb))""",
        id=uuid4(),
        invocation=invocation_id,
        phase=phase,
        now=now,
        metadata=json.dumps(metadata, default=str),
    )


def adjust(
    conn, invocation, state, active, input_tokens, output_tokens, now, **metadata
):
    record(
        conn,
        invocation["id"],
        state,
        now,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        **metadata,
    )
    run(
        conn,
        """UPDATE ai_usage_daily SET attempts=attempts-:refund,
        input_tokens=input_tokens+:di,output_tokens=output_tokens+:do
        WHERE tenant_id=:tid AND day=:day""",
        day=invocation["day"],
        refund=int(state == "CANCELED"),
        di=input_tokens - invocation["input_tokens"],
        do=output_tokens - invocation["output_tokens"],
    )
    run(
        conn,
        """UPDATE ai_invocations SET state=:state,active=:active,
        input_tokens=:it,output_tokens=:ot WHERE tenant_id=:tid AND id=:id""",
        id=invocation["id"],
        state=state,
        active=active,
        it=input_tokens,
        ot=output_tokens,
    )


def reap(conn, now):
    for row in rows(
        conn,
        """SELECT * FROM ai_invocations WHERE tenant_id=:tid
        AND active AND lease_until<=:now FOR UPDATE""",
        now=now,
    ):
        canceled = row["state"] == "RESERVED"
        adjust(
            conn,
            row,
            "CANCELED" if canceled else "UNKNOWN",
            False,
            0 if canceled else row["input_tokens"],
            0 if canceled else row["output_tokens"],
            now,
            reason="lease_expired",
        )


def reserve(ctx, prompt_hash, sources, template):
    now = ctx.service.ai_clock()
    advisory_lock(ctx.conn, f"ai:{ctx.p.tenant_id}")
    reap(ctx.conn, now)
    run(
        ctx.conn,
        """INSERT INTO ai_usage_daily(tenant_id,day) VALUES(:tid,:day)
        ON CONFLICT DO NOTHING""",
        day=now.date(),
    )
    daily = one(
        ctx.conn,
        "SELECT * FROM ai_usage_daily WHERE tenant_id=:tid AND day=:day FOR UPDATE",
        day=now.date(),
    )
    active = one(
        ctx.conn,
        "SELECT count(*) AS n FROM ai_invocations WHERE tenant_id=:tid AND active",
    )["n"]
    if (
        active >= 2
        or daily["attempts"] >= 100
        or daily["input_tokens"] + INPUT_RESERVATION > 1000000
        or daily["output_tokens"] + OUTPUT_RESERVATION > 100000
    ):
        metrics.emit(
            {"AILimitDenials": 1}, tenant_id=ctx.p.tenant_id, request_id=ctx.request_id
        )
        raise APIError(429, "AI_LIMIT_EXCEEDED", headers={"Retry-After": "60"})
    iid = uuid4()
    p = ctx.p
    run(
        ctx.conn,
        """INSERT INTO ai_invocations
        (id,tenant_id,day,request_id,account_id,client_id,grant_id,asu_id,credential_version_id,
         by_user_account_id,on_behalf_of_user_account_id,model,template_version,prompt_sha256,sources,
         state,active,created_at,lease_until,input_tokens,output_tokens)
        VALUES(:id,:tid,:day,:rid,:aid,:cid,:gid,:asu,:credential,:by,:behalf,
         'mw-small-text-v1',:template,:hash,CAST(:sources AS jsonb),'RESERVED',true,:now,:lease,:it,:ot)""",
        id=iid,
        day=now.date(),
        rid=ctx.request_id,
        aid=p.account_id,
        cid=p.client_id,
        gid=p.grant_id,
        asu=p.asu_id,
        credential=p.credential_version_id,
        by=p.actor_account_id or p.account_id,
        behalf=p.account_id if p.asu_id and p.kind == "delegated" else None,
        template=template,
        hash=prompt_hash,
        sources=json.dumps(sources, default=str),
        now=now,
        lease=now + timedelta(seconds=60),
        it=INPUT_RESERVATION,
        ot=OUTPUT_RESERVATION,
    )
    record(
        ctx.conn,
        iid,
        "ATTEMPT",
        now,
        prompt_sha256=prompt_hash,
        sources=sources,
        template_version=template,
        request_id=ctx.request_id,
        model="mw-small-text-v1",
        account_id=p.account_id,
        client_id=p.client_id,
        grant_id=p.grant_id,
        asu_id=p.asu_id,
        credential_version_id=p.credential_version_id,
        by_user_account_id=p.actor_account_id or p.account_id,
        on_behalf_of_user_account_id=p.account_id
        if p.asu_id and p.kind == "delegated"
        else None,
    )
    run(
        ctx.conn,
        """UPDATE ai_usage_daily SET attempts=attempts+1,
        input_tokens=input_tokens+:it,output_tokens=output_tokens+:ot
        WHERE tenant_id=:tid AND day=:day""",
        day=now.date(),
        it=INPUT_RESERVATION,
        ot=OUTPUT_RESERVATION,
    )
    return iid


def dispatch(service, tid, iid):
    with service.db.tenant_tx(tid) as conn:
        advisory_lock(conn, f"ai:{tid}")
        row = one(
            conn,
            "SELECT * FROM ai_invocations WHERE tenant_id=:tid AND id=:id FOR UPDATE",
            id=iid,
        )
        if (
            row["state"] != "RESERVED"
            or not row["active"]
            or row["lease_until"] <= service.ai_clock()
        ):
            raise APIError(503, "AI_UNAVAILABLE")
        record(conn, iid, "DISPATCHED", service.ai_clock())
        run(
            conn,
            "UPDATE ai_invocations SET state='DISPATCHED' WHERE tenant_id=:tid AND id=:id",
            id=iid,
        )


def settle(service, tid, iid, state, usage=None, **metadata):
    with service.db.tenant_tx(tid) as conn:
        advisory_lock(conn, f"ai:{tid}")
        row = one(
            conn,
            "SELECT * FROM ai_invocations WHERE tenant_id=:tid AND id=:id FOR UPDATE",
            id=iid,
        )
        if row["state"] not in ("RESERVED", "DISPATCHED", "UNKNOWN"):
            return False
        if state == "CANCELED" and row["state"] != "RESERVED":
            return False
        it, ot = (
            usage if usage is not None else (row["input_tokens"], row["output_tokens"])
        )
        adjust(conn, row, state, False, it, ot, service.ai_clock(), **metadata)
    return True
