from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from ..authz import Target, descendants, filled_assignments, job_as_of, worker_target
from ..db import one, rows
from ..pagination import page
from .models import (
    Compensation,
    HistoryItem,
    Organization,
    Reference,
    Worker,
    WorkerPage,
)

router = APIRouter(prefix="/api/v1")
Limit = Annotated[int, Query(ge=1, le=200)]


def ref(kind, row, name="name"):
    return {
        "id": row["id"].hex,
        "descriptor": row[name],
        "href": f"/api/v1/{kind}/{row['id'].hex}",
    }


def org_ref(conn, oid):
    return ref(
        "organizations",
        one(
            conn, "SELECT * FROM organizations WHERE tenant_id=:tid AND id=:id", id=oid
        ),
    )


def worker_shape(ctx, row, job):
    return {
        **ref("workers", row),
        "employeeId": row["employee_id"],
        "active": row["active"],
        "primaryPosition": {
            "id": job["position_id"].hex,
            "descriptor": job["title"],
            "href": f"/api/v1/positions/{job['position_id'].hex}",
        },
        "primarySupervisoryOrganization": org_ref(ctx.conn, job["org_id"]),
    }


def visible_worker(ctx, wid, day):
    target = worker_target(ctx.conn, wid, ctx.now)
    worker = one(
        ctx.conn, "SELECT * FROM workers WHERE tenant_id=:tid AND id=:id", id=wid
    )
    job = job_as_of(ctx.conn, wid, day)
    if not worker or not job:
        ctx.not_found(target)
    ctx.require("READ", "WORKER_BASIC", target, status=404)
    return worker, job, target


def workers_page(
    ctx,
    path,
    day,
    org,
    include_subordinates,
    employee_id,
    limit,
    cursor,
    report_ids=None,
):
    orgs = None
    if org:
        orgs = {org} | (descendants(ctx.conn, org) if include_subordinates else set())
    result = []
    for worker in rows(
        ctx.conn, "SELECT * FROM workers WHERE tenant_id=:tid ORDER BY id"
    ):
        if report_ids is not None and worker["id"] not in report_ids:
            continue
        if employee_id and worker["employee_id"] != employee_id:
            continue
        job = job_as_of(ctx.conn, worker["id"], day)
        if not job or (orgs is not None and job["org_id"] not in orgs):
            continue
        target = worker_target(ctx.conn, worker["id"], ctx.now)
        if ctx.check("READ", "WORKER_BASIC", target):
            result.append(worker_shape(ctx, worker, job))
    return page(
        result,
        ctx,
        path,
        {
            "as_of": day,
            "org": org,
            "include_subordinates": include_subordinates,
            "employee_id": employee_id,
        },
        limit,
        cursor,
        ctx.service.cursor_secret,
    )


@router.get("/workers", response_model=WorkerPage)
def list_workers(
    request: Request,
    org: UUID | None = None,
    include_subordinates: bool = False,
    employee_id: str | None = None,
    as_of: date | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
):
    with request.app.state.service.request(request) as ctx:
        return workers_page(
            ctx,
            request.url.path,
            as_of or ctx.now.date(),
            org,
            include_subordinates,
            employee_id,
            limit,
            cursor,
        )


@router.get("/workers/{wid}", response_model=Worker)
def get_worker(request: Request, wid: UUID, as_of: date | None = None):
    with request.app.state.service.request(request) as ctx:
        worker, job, _ = visible_worker(ctx, wid, as_of or ctx.now.date())
        return worker_shape(ctx, worker, job)


@router.get("/workers/{wid}/organizations", response_model=Reference)
def worker_organizations(request: Request, wid: UUID, as_of: date | None = None):
    with request.app.state.service.request(request) as ctx:
        _, job, target = visible_worker(ctx, wid, as_of or ctx.now.date())
        ctx.require("READ", "WORKER_ORGANIZATIONS", target)
        return org_ref(ctx.conn, job["org_id"])


@router.get("/workers/{wid}/compensation", response_model=Compensation)
def compensation(request: Request, wid: UUID, as_of: date | None = None):
    with request.app.state.service.request(request) as ctx:
        day = as_of or ctx.now.date()
        worker, _, target = visible_worker(ctx, wid, day)
        comp = one(
            ctx.conn,
            """SELECT * FROM compensation_revisions WHERE tenant_id=:tid AND worker_id=:id AND effective_date<=:day
            ORDER BY effective_date DESC, recorded_seq DESC LIMIT 1""",
            id=wid,
            day=day,
        )
        if not comp:
            ctx.not_found(target)
        ctx.require("READ", "WORKER_COMPENSATION", target, sensitive=True)
        return {
            "worker": ref("workers", worker),
            "annualSalary": float(comp["annual_salary"]),
            "currency": comp["currency"],
            "effectiveDate": comp["effective_date"].isoformat(),
        }


@router.get(
    "/workers/{wid}/history",
    response_model=list[HistoryItem],
    response_model_exclude_none=True,
)
def history(request: Request, wid: UUID, as_of: date | None = None):
    with request.app.state.service.request(request) as ctx:
        day = as_of or ctx.now.date()
        _, _, target = visible_worker(ctx, wid, day)
        include_comp = ctx.check("READ", "WORKER_COMPENSATION", target, sensitive=True)
        result = []
        for row in rows(
            ctx.conn,
            """SELECT j.*,p.title FROM job_revisions j
                JOIN positions p ON p.tenant_id=:tid AND p.id=j.position_id
                WHERE j.tenant_id=:tid AND j.worker_id=:id AND j.effective_date<=:day
                ORDER BY j.effective_date,j.recorded_seq""",
            id=wid,
            day=day,
        ):
            result.append(
                {
                    "type": "JOB",
                    "effectiveDate": row["effective_date"].isoformat(),
                    "recordedAt": row["recorded_at"].isoformat(),
                    "position": {
                        "id": row["position_id"].hex,
                        "descriptor": row["title"],
                        "href": f"/api/v1/positions/{row['position_id'].hex}",
                    },
                }
            )
        if include_comp:
            for row in rows(
                ctx.conn,
                """SELECT * FROM compensation_revisions
                    WHERE tenant_id=:tid AND worker_id=:id AND effective_date<=:day ORDER BY effective_date,recorded_seq""",
                id=wid,
                day=day,
            ):
                result.append(
                    {
                        "type": "COMPENSATION",
                        "effectiveDate": row["effective_date"].isoformat(),
                        "recordedAt": row["recorded_at"].isoformat(),
                        "annualSalary": float(row["annual_salary"]),
                        "currency": row["currency"],
                    }
                )
        return sorted(
            result, key=lambda r: (r["effectiveDate"], r["recordedAt"], r["type"])
        )


@router.get("/workers/{wid}/direct-reports", response_model=WorkerPage)
def direct_reports(
    request: Request,
    wid: UUID,
    as_of: date | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
):
    with request.app.state.service.request(request) as ctx:
        day = as_of or ctx.now.date()
        visible_worker(ctx, wid, day)
        assignments = filled_assignments(
            ctx.conn, ctx.now.replace(year=day.year, month=day.month, day=day.day)
        )
        managed = {
            r["org_id"]
            for r in assignments
            if r["role"] == "MANAGER" and r["worker_id"] == wid
        }
        child_orgs = {
            r["id"]
            for r in rows(
                ctx.conn,
                "SELECT id,superior_id FROM organizations WHERE tenant_id=:tid",
            )
            if r["superior_id"] in managed
        }
        report_ids = {
            r["worker_id"]
            for r in assignments
            if r["role"] == "MANAGER" and r["org_id"] in child_orgs
        }
        for worker in rows(ctx.conn, "SELECT id FROM workers WHERE tenant_id=:tid"):
            job = job_as_of(ctx.conn, worker["id"], day)
            if job and job["org_id"] in managed:
                report_ids.add(worker["id"])
        return workers_page(
            ctx, request.url.path, day, None, False, None, limit, cursor, report_ids
        )


@router.get("/organizations/{wid}", response_model=Organization)
def organization(request: Request, wid: UUID):
    with request.app.state.service.request(request) as ctx:
        org = one(
            ctx.conn,
            "SELECT * FROM organizations WHERE tenant_id=:tid AND id=:id",
            id=wid,
        )
        if not org:
            ctx.not_found(Target("organizations", wid))
        return {
            **ref("organizations", org),
            "refId": org["ref_id"],
            "superior": org_ref(ctx.conn, org["superior_id"])
            if org["superior_id"]
            else None,
        }


@router.get("/organizations/{wid}/workers", response_model=WorkerPage)
def organization_workers(
    request: Request,
    wid: UUID,
    as_of: date | None = None,
    include_subordinates: bool = False,
    limit: Limit = 50,
    cursor: str | None = None,
):
    with request.app.state.service.request(request) as ctx:
        if not one(
            ctx.conn,
            "SELECT id FROM organizations WHERE tenant_id=:tid AND id=:id",
            id=wid,
        ):
            ctx.not_found(Target("organizations", wid))
        return workers_page(
            ctx,
            request.url.path,
            as_of or ctx.now.date(),
            wid,
            include_subordinates,
            None,
            limit,
            cursor,
        )
