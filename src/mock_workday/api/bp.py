from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from .. import bp
from ..authz import Target
from ..db import rows
from ..idempotency import perform
from ..pagination import page
from .models import (
    ActionInput,
    ChangeJobInput,
    OperationReceipt,
    ProcessEvent,
    ProcessPage,
    TimeOffInput,
)
from .workers import Limit

router = APIRouter(prefix="/api/v1")
IDEMPOTENCY_HEADER = {
    "parameters": [
        {
            "name": "Idempotency-Key",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "minLength": 1, "maxLength": 128},
        }
    ]
}


def operation_response(ctx, request, operation, status):
    result, code, headers = perform(ctx, request, operation, status)
    return JSONResponse(
        jsonable_encoder(result),
        status_code=code,
        headers={"X-Request-Id": ctx.request_id, **headers},
    )


@router.post(
    "/business-processes/change-job",
    status_code=201,
    response_model=OperationReceipt,
    openapi_extra=IDEMPOTENCY_HEADER,
)
def change_job(request: Request, body: ChangeJobInput):
    with request.app.state.service.request(request) as ctx:
        return operation_response(
            ctx, request, lambda: bp.initiate(ctx, "CHANGE_JOB", body), 201
        )


@router.post(
    "/business-processes/request-time-off",
    status_code=201,
    response_model=OperationReceipt,
    openapi_extra=IDEMPOTENCY_HEADER,
)
def time_off(request: Request, body: TimeOffInput):
    with request.app.state.service.request(request) as ctx:
        return operation_response(
            ctx, request, lambda: bp.initiate(ctx, "REQUEST_TIME_OFF", body), 201
        )


@router.get(
    "/business-process-events/{wid}",
    response_model=ProcessEvent,
    response_model_exclude_unset=True,
)
def get_event(request: Request, wid: UUID):
    with request.app.state.service.request(request) as ctx:
        event = bp.event_row(ctx, wid)
        if not event:
            ctx.not_found(Target("business-process-events", wid))
        steps = bp.event_steps(ctx, wid)
        bp.require_view(ctx, event, steps)
        return bp.event_shape(ctx, event, steps)


@router.get(
    "/business-process-events",
    response_model=ProcessPage,
    response_model_exclude_unset=True,
)
def list_events(
    request: Request,
    awaiting_me: bool = False,
    status: str | None = None,
    subject: UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
):
    with request.app.state.service.request(request) as ctx:
        candidates = []
        for event in rows(
            ctx.conn, "SELECT * FROM bp_events WHERE tenant_id=:tid ORDER BY id"
        ):
            if (
                status
                and event["status"] != status
                or subject
                and event["subject_worker_id"] != subject
            ):
                continue
            steps = bp.event_steps(ctx, event["id"])
            if not bp.can_view(ctx, event, steps):
                continue
            if awaiting_me and not any(
                s["status"] == "AWAITING"
                and ctx.p.account_id in bp.current_assignees(ctx, event, s)
                for s in steps
            ):
                continue
            candidates.append(bp.reference(event))
        result = page(
            candidates,
            ctx,
            request.url.path,
            {"awaiting_me": awaiting_me, "status": status, "subject": subject},
            limit,
            cursor,
            ctx.service.cursor_secret,
        )
        events = []
        for ref in result["data"]:
            event = bp.event_row(ctx, UUID(ref["id"]))
            events.append(bp.event_shape(ctx, event, bp.event_steps(ctx, event["id"])))
        result["data"] = events
        return result


@router.post(
    "/business-process-events/{wid}/approve",
    response_model=OperationReceipt,
    openapi_extra=IDEMPOTENCY_HEADER,
)
def approve(request: Request, wid: UUID, body: ActionInput):
    with request.app.state.service.request(request) as ctx:
        return operation_response(
            ctx, request, lambda: bp.act(ctx, wid, "approve", body), 200
        )


@router.post(
    "/business-process-events/{wid}/deny",
    response_model=OperationReceipt,
    openapi_extra=IDEMPOTENCY_HEADER,
)
def deny(request: Request, wid: UUID, body: ActionInput):
    with request.app.state.service.request(request) as ctx:
        return operation_response(
            ctx, request, lambda: bp.act(ctx, wid, "deny", body), 200
        )


@router.post(
    "/business-process-events/{wid}/cancel",
    response_model=OperationReceipt,
    openapi_extra=IDEMPOTENCY_HEADER,
)
def cancel(request: Request, wid: UUID, body: ActionInput):
    with request.app.state.service.request(request) as ctx:
        return operation_response(
            ctx, request, lambda: bp.act(ctx, wid, "cancel", body), 200
        )
