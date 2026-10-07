import hashlib
import hmac
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import reports
from ..db import one
from ..errors import APIError, request_id

router = APIRouter(prefix="/api/v1")
download_router = APIRouter(prefix="/api/v1")


class ExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report: Literal["worker-roster"]
    as_of: date | None = None
    include_compensation: bool = False


class ExportResult(BaseModel):
    id: str
    report: Literal["worker-roster"]
    row_count: int = Field(ge=0, le=10000)
    byte_length: int = Field(ge=0, le=16777216)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    created_at: datetime
    expires_at: datetime
    download_url: str = Field(
        description="GET-only bearer capability, at most 60 seconds and capped by caller and signing-session lifetimes. Never log or forward to an untrusted recipient."
    )


@router.post(
    "/report-exports",
    response_model=ExportResult,
    status_code=201,
    description="Synchronous worker-roster NDJSON snapshot. Current row/field authority applies; compensation is omitted unless requested and authorized. Maximum 10,000 rows / 16 MiB; overflow is 422 REPORT_TOO_LARGE. Audit commits before returning a download capability. No idempotency guarantee.",
)
def create_export(request: Request, body: ExportInput, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with request.app.state.service.request(request, snapshot=True) as ctx:
        result = reports.create(ctx, body, request)
    if result["expires_at"] <= reports.wall_now():
        raise APIError(401, "UNAUTHENTICATED")
    return result


@download_router.get(
    "/report-exports/{export_id}/download",
    response_class=Response,
    responses={
        200: {"content": {"application/x-ndjson": {"schema": {"type": "string"}}}}
    },
)
def download_export(
    request: Request,
    export_id: UUID,
    expires: int,
    signature: str = Query(max_length=64),
):
    service = request.app.state.service
    tenant = service.tenant(request)
    expected = reports.signature(
        service.export_secret, tenant["id"], export_id, expires
    )
    if (
        service.storage.mapping
        or not hmac.compare_digest(signature.encode(), expected.encode())
        or expires <= reports.wall_now().timestamp()
    ):
        raise APIError(404, "NOT_FOUND")
    with service.db.tenant_tx(tenant["id"]) as conn:
        item = one(
            conn,
            "SELECT * FROM report_exports WHERE tenant_id=:tid AND id=:id",
            id=export_id,
        )
        if (
            not item
            or int(item["expires_at"].timestamp()) != expires
            or item["content"] is None
        ):
            raise APIError(404, "NOT_FOUND")
        content = bytes(item["content"])
        if (
            len(content) != item["byte_length"]
            or hashlib.sha256(content).hexdigest() != item["sha256"]
        ):
            raise APIError(503, "SERVICE_UNAVAILABLE")
    request.state.identity_context = {"export_id": export_id.hex}
    return Response(
        content,
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Request-Id": request_id(request)},
    )
