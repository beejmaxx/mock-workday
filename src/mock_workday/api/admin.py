from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError

from ..auth import rotate_key
from ..clock import SEED_TIME
from ..db import one, run
from ..errors import APIError
from ..seed import seed

router = APIRouter(prefix="/admin")


class TenantBody(BaseModel):
    slug: str


class ClockBody(BaseModel):
    now: datetime | None = None
    advance_seconds: float | None = None

    @model_validator(mode="after")
    def clock_value(self):
        if (self.now is None) == (self.advance_seconds is None):
            raise ValueError("Provide exactly one clock value")
        if self.now is not None and self.now.tzinfo is None:
            raise ValueError("Clock time needs a timezone")
        return self


class RoleBody(TenantBody):
    role: Literal["MANAGER", "HR_PARTNER", "COMPENSATION_PARTNER"]
    org_id: UUID
    position_id: UUID


class RateBody(TenantBody):
    client_id: str
    capacity: int = Field(ge=1)
    refill_per_second: float = Field(gt=0)


def tenant_id(service, slug):
    with service.db.owner.connect() as conn:
        row = one(conn, "SELECT id FROM tenants WHERE slug=:slug", slug=slug)
    if not row:
        raise APIError(404, "TENANT_NOT_FOUND")
    return row["id"]


@router.post("/clock")
def clock(request: Request, body: ClockBody):
    clock = request.app.state.service.clock
    if body.now is not None:
        clock.set(body.now)
    else:
        clock.advance(body.advance_seconds)
    return {"now": clock.now()}


@router.post("/reset")
def reset(request: Request):
    service = request.app.state.service
    seed(service.db)
    service.clock.set(SEED_TIME)
    service.rate_limits.reset()
    service.faults.clear()
    return {"reset": True}


@router.post("/role-assignments", status_code=201)
def assign(request: Request, body: RoleBody):
    service = request.app.state.service
    tid = tenant_id(service, body.slug)
    try:
        with service.db.tenant_tx(tid, owner=True) as conn:
            rid = uuid4()
            run(
                conn,
                """INSERT INTO role_assignments (id,tenant_id,role,org_id,position_id,assigned_at)
                VALUES (:id,:tid,:role,:org,:pos,:now)""",
                id=rid,
                role=body.role,
                org=body.org_id,
                pos=body.position_id,
                now=service.clock.now(),
            )
            run(
                conn,
                "UPDATE tenant_config SET policy_version=policy_version+1 WHERE tenant_id=:tid",
            )
            return {"id": rid.hex}
    except IntegrityError:
        raise APIError(422, "VALIDATION_ERROR") from None


@router.post("/role-assignments/{wid}/revoke")
def revoke(request: Request, wid: UUID, body: TenantBody):
    service = request.app.state.service
    with service.db.tenant_tx(tenant_id(service, body.slug), owner=True) as conn:
        role = one(
            conn,
            "SELECT * FROM role_assignments WHERE tenant_id=:tid AND id=:id FOR UPDATE",
            id=wid,
        )
        if not role:
            raise APIError(404, "NOT_FOUND")
        if role["revoked_at"] is None:
            run(
                conn,
                "UPDATE role_assignments SET revoked_at=:now WHERE tenant_id=:tid AND id=:id",
                id=wid,
                now=service.clock.now(),
            )
            run(
                conn,
                "UPDATE tenant_config SET policy_version=policy_version+1 WHERE tenant_id=:tid",
            )
    return {"id": wid.hex}


def disable(service, slug, table, wid):
    with service.db.tenant_tx(tenant_id(service, slug), owner=True) as conn:
        result = run(
            conn,
            f"UPDATE {table} SET disabled=true WHERE tenant_id=:tid AND id=:id",
            id=wid,
        )
        if not result.rowcount:
            raise APIError(404, "NOT_FOUND")
    return {"id": wid.hex}


@router.post("/accounts/{wid}/disable")
def disable_account(request: Request, wid: UUID, body: TenantBody):
    return disable(request.app.state.service, body.slug, "accounts", wid)


@router.post("/api-clients/{wid}/disable")
def disable_client(request: Request, wid: UUID, body: TenantBody):
    return disable(request.app.state.service, body.slug, "api_clients", wid)


@router.post("/signing-keys/rotate")
def rotate(request: Request):
    service = request.app.state.service
    with service.db.owner.begin() as conn:
        return {"kid": rotate_key(conn, service.clock.now())}


@router.post("/signing-keys/{kid}/retire")
def retire(request: Request, kid: UUID):
    with request.app.state.service.db.owner.begin() as conn:
        if not run(
            conn, "UPDATE signing_keys SET state='RETIRED' WHERE id=:id", id=kid
        ).rowcount:
            raise APIError(404, "NOT_FOUND")
    return {"kid": kid.hex}


@router.post("/rate-limits")
def rate_limit(request: Request, body: RateBody):
    service = request.app.state.service
    service.rate_limits.configure(
        tenant_id(service, body.slug),
        body.client_id,
        body.capacity,
        body.refill_per_second,
    )
    return {"configured": True}


class FaultMatch(BaseModel):
    method: str
    path_prefix: str
    client_id: str | None = None


class FaultBody(TenantBody):
    match: FaultMatch
    type: Literal[
        "latency",
        "status",
        "timeout_before_commit",
        "timeout_after_commit",
        "audit_write_failure",
    ]
    value: int | None = None
    count: int = Field(ge=1)

    @model_validator(mode="after")
    def fault_value(self):
        if self.type == "latency" and (self.value is None or self.value < 0):
            raise ValueError("Latency must be nonnegative milliseconds")
        if self.type == "status" and self.value not in (429, 503):
            raise ValueError("Status must be 429 or 503")
        return self


@router.post("/faults", status_code=201)
def add_fault(request: Request, body: FaultBody):
    service = request.app.state.service
    rule = body.model_dump(exclude={"slug"})
    rule["match"]["method"] = rule["match"]["method"].upper()
    service.faults.add(tenant_id(service, body.slug), rule)
    return {"configured": True}


@router.delete("/faults", status_code=204)
def clear_faults(request: Request, body: TenantBody):
    service = request.app.state.service
    service.faults.clear(tenant_id(service, body.slug))
