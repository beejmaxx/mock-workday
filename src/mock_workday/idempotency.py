import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

from anyio.from_thread import run as from_thread
from fastapi.encoders import jsonable_encoder

from . import bp
from .db import advisory_lock, one, run
from .errors import APIError


def perform(ctx, request, operation, status_code):
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED")
    if len(key) > 128:
        raise APIError(400, "BAD_REQUEST")
    # Hash the original JSON, including fields that model validation may normalize.
    digest = hashlib.sha256(
        json.dumps(
            [request.method, request.url.path, from_thread(request.json)],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    client_key = ctx.p.client_id or "-"
    advisory_lock(
        ctx.conn, f"idem:{ctx.p.tenant_id}:{ctx.p.account_id}:{client_key}:{key}"
    )
    ctx.now = ctx.service.clock.now()
    binding = {"account": ctx.p.account_id, "client": client_key, "key": key}
    record = one(
        ctx.conn,
        """SELECT * FROM idempotency_records
        WHERE tenant_id=:tid AND account_id=:account AND client_key=:client AND idem_key=:key AND expires_at>:now""",
        **binding,
        now=ctx.now,
    )
    if record:
        if record["request_hash"] != digest:
            raise APIError(422, "IDEMPOTENCY_KEY_REUSED")
        result = record["receipt"].copy()
        event = bp.event_row(ctx, UUID(result["resource"]["id"]))
        if event:
            steps = bp.event_steps(ctx, event["id"])
            if bp.can_view(ctx, event, steps):
                result["event"] = bp.disclose(
                    ctx, event, steps, record["response"]["event"]
                )
        return result, record["status_code"], {"Idempotent-Replay": "true"}
    run(
        ctx.conn,
        """DELETE FROM idempotency_records
        WHERE tenant_id=:tid AND account_id=:account AND client_key=:client AND idem_key=:key AND expires_at<=:now""",
        **binding,
        now=ctx.now,
    )
    event = operation()
    receipt = {
        "operation_id": uuid4().hex,
        "status": event["status"],
        "resource": bp.reference(event),
    }
    result = jsonable_encoder(
        {
            **receipt,
            "event": bp.event_shape(ctx, event, bp.event_steps(ctx, event["id"])),
        }
    )
    run(
        ctx.conn,
        """INSERT INTO idempotency_records VALUES
        (:tid,:account,:client,:key,:hash,:operation,:status,CAST(:receipt AS jsonb),CAST(:response AS jsonb),:now,:expires)""",
        **binding,
        hash=digest,
        operation=UUID(receipt["operation_id"]),
        status=status_code,
        receipt=json.dumps(receipt),
        response=json.dumps(result),
        now=ctx.now,
        expires=ctx.now + timedelta(hours=24),
    )
    return result, status_code, {}
