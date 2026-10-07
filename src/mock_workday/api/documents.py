from uuid import UUID, uuid4

from fastapi import APIRouter, Request

from ..audit import object_record
from ..authz import Target, worker_target
from ..db import one, rows
from ..errors import APIError
from ..pagination import page
from .models import Document, DocumentInput, DocumentMetadata, DocumentPage
from .workers import Limit, org_ref, ref

router = APIRouter(prefix="/api/v1/documents")


def target_for(ctx, doc):
    if doc["owner_worker_id"]:
        return worker_target(
            ctx.conn,
            doc["owner_worker_id"],
            ctx.now,
            resource_type="documents",
            resource_id=doc["id"],
        )
    return Target("documents", doc["id"], org_id=doc["org_id"])


def metadata(ctx, doc):
    owner = (
        one(
            ctx.conn,
            "SELECT * FROM workers WHERE tenant_id=:tid AND id=:id",
            id=doc["owner_worker_id"],
        )
        if doc["owner_worker_id"]
        else None
    )
    return {
        **ref("documents", doc, "title"),
        "title": doc["title"],
        "domain": doc["domain"],
        "classification": doc["classification"],
        "owner": ref("workers", owner) if owner else None,
        "org": org_ref(ctx.conn, doc["org_id"]) if doc["org_id"] else None,
        "created_at": doc["created_at"],
    }


@router.get("", response_model=DocumentPage)
def list_documents(
    request: Request,
    owner_worker_id: UUID | None = None,
    org_id: UUID | None = None,
    limit: Limit = 50,
    cursor: str | None = None,
):
    with request.app.state.service.request(request) as ctx:
        result = []
        # Content is not fetched by the metadata-only list route.
        for doc in rows(
            ctx.conn,
            """SELECT id,title,domain,classification,owner_worker_id,org_id,created_at
                FROM documents WHERE tenant_id=:tid ORDER BY id""",
        ):
            if owner_worker_id and doc["owner_worker_id"] != owner_worker_id:
                continue
            if org_id and doc["org_id"] != org_id:
                continue
            if ctx.check("READ", doc["domain"], target_for(ctx, doc)):
                result.append(metadata(ctx, doc))
        return page(
            result,
            ctx,
            request.url.path,
            {"owner_worker_id": owner_worker_id, "org_id": org_id},
            limit,
            cursor,
            ctx.service.cursor_secret,
        )


@router.get("/{wid}", response_model=Document)
def get_document(request: Request, wid: UUID):
    with request.app.state.service.request(request) as ctx:
        doc = one(
            ctx.conn, "SELECT * FROM documents WHERE tenant_id=:tid AND id=:id", id=wid
        )
        if not doc:
            ctx.not_found(Target("documents", wid))
        ctx.require(
            "READ",
            doc["domain"],
            target_for(ctx, doc),
            sensitive=doc["classification"] in ("CONFIDENTIAL", "RESTRICTED"),
            status=404,
        )
        return {**metadata(ctx, doc), "content": doc["content"]}


@router.post("", status_code=201, response_model=DocumentMetadata)
def create_document(request: Request, body: DocumentInput):
    with request.app.state.service.request(request) as ctx:
        valid = (
            (
                body.domain == "DOC_TENANT"
                and body.org_id is None
                and body.owner_worker_id is None
            )
            or (
                body.domain == "DOC_ORG"
                and body.org_id is not None
                and body.owner_worker_id is None
            )
            or (
                body.domain == "DOC_WORKER"
                and body.owner_worker_id is not None
                and body.org_id is None
            )
        )
        levels = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
        if not valid or levels.index(body.classification) < (
            1 if body.domain == "DOC_TENANT" else 2
        ):
            raise APIError(422, "VALIDATION_ERROR")
        for table, oid in [
            ("organizations", body.org_id),
            ("workers", body.owner_worker_id),
        ]:
            if oid and not one(
                ctx.conn,
                f"SELECT id FROM {table} WHERE tenant_id=:tid AND id=:id",
                id=oid,
            ):
                ctx.not_found(Target(table, oid), action="WRITE")
        did = uuid4()
        values = body.model_dump() | {"id": did}
        ctx.require("WRITE", body.domain, target_for(ctx, values))
        doc = one(
            ctx.conn,
            """INSERT INTO documents
            (id,tenant_id,title,content,domain,classification,owner_worker_id,org_id,
             created_by_account_id,created_by_client_id,created_at)
            VALUES (:id,:tid,:title,:content,:domain,:classification,:owner_worker_id,:org_id,:aid,:cid,:now)
            RETURNING id,title,domain,classification,owner_worker_id,org_id,created_at""",
            **values,
            aid=ctx.p.account_id,
            cid=ctx.p.client_id,
            now=ctx.now,
        )
        object_record(
            ctx.conn,
            ctx.p,
            ctx.request_id,
            "documents",
            did,
            "created",
            None,
            values,
            ctx.now,
        )
        return metadata(ctx, doc)
