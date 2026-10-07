import hashlib
import hmac
import json
import logging
import math
import tempfile
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError

from . import audit
from .api.workers import ref, worker_shape
from .authz import Decision, Target, job_as_of, worker_target
from .db import one, run
from .errors import APIError

MAX_ROWS = 10000
MAX_BYTES = 16 * 1024 * 1024


def wall_now():
    return datetime.now(UTC)


def signature(secret, tenant_id, export_id, expires):
    value = f"{tenant_id}:{export_id}:{expires}:GET".encode()
    return hmac.new(secret, value, hashlib.sha256).hexdigest()


def export_location(storage, tenant, export_id):
    destination, _ = storage.location(tenant, export_id)
    return destination, f"tenants/{tenant['id']}/exports/{export_id}"


def cleanup_export(storage, tenant, export_id, request_id):
    if not storage.mapping:
        return
    try:
        destination, key = export_location(storage, tenant, export_id)
        storage.client(tenant).delete_object(Bucket=destination["bucket"], Key=key)
    except (BotoCoreError, ClientError, APIError):
        logging.getLogger(__name__).warning(
            json.dumps(
                {
                    "event": "export_orphan",
                    "request_id": request_id,
                    "tenant_id": str(tenant["id"]),
                    "export_id": str(export_id),
                }
            )
        )


def authority_deadline(ctx):
    deadlines = [ctx.p.expires_at]
    if ctx.p.grant_id:
        grant = one(
            ctx.conn,
            "SELECT expires_at FROM delegation_grants WHERE tenant_id=:tid AND id=:id",
            id=ctx.p.grant_id,
        )
        deadlines.append(grant["expires_at"].timestamp())
    if ctx.p.credential_version_id:
        version = one(
            ctx.conn,
            "SELECT accept_until,certificate_expires_at FROM credential_versions WHERE tenant_id=:tid AND id=:id",
            id=ctx.p.credential_version_id,
        )
        deadlines.extend(value.timestamp() for value in version.values() if value)
    remaining = min(deadlines) - ctx.service.clock.now().timestamp()
    if remaining < 1:
        raise APIError(401, "UNAUTHENTICATED")
    # A frozen business clock must not extend a capability while a report is built.
    return wall_now() + timedelta(seconds=remaining)


def roster(ctx, body):
    day = body.as_of or ctx.now.date()
    for worker in run(
        ctx.conn, "SELECT * FROM workers WHERE tenant_id=:tid ORDER BY id"
    ).mappings():
        job = job_as_of(ctx.conn, worker["id"], day)
        target = worker_target(ctx.conn, worker["id"], ctx.now)
        if not job or not ctx.visible_in_list("WORKER_BASIC", target):
            continue
        result = worker_shape(ctx, worker, job)
        if body.include_compensation and ctx.visible_in_list(
            "WORKER_COMPENSATION", target
        ):
            comp = one(
                ctx.conn,
                """SELECT * FROM compensation_revisions
                WHERE tenant_id=:tid AND worker_id=:id AND effective_date<=:day
                ORDER BY effective_date DESC,recorded_seq DESC LIMIT 1""",
                id=worker["id"],
                day=day,
            )
            if comp:
                ctx.require("READ", "WORKER_COMPENSATION", target, sensitive=True)
                result["compensation"] = {
                    "worker": ref("workers", worker),
                    "annualSalary": float(comp["annual_salary"]),
                    "currency": comp["currency"],
                    "effectiveDate": comp["effective_date"].isoformat(),
                }
        yield result


def create(ctx, body, request):
    needed = {"staffing"} | ({"compensation"} if body.include_compensation else set())
    permitted = ctx.p.scopes is None or needed <= ctx.p.scopes
    policy = one(
        ctx.conn, "SELECT policy_version FROM tenant_config WHERE tenant_id=:tid"
    )["policy_version"]
    ctx.record_decision(
        "READ",
        None,
        Target("report-exports", None),
        Decision(permitted, "REPORT_SCOPE", None, None, policy, None),
        sensitive=True,
    )
    if not permitted:
        raise APIError(403, "FORBIDDEN")
    deadline = authority_deadline(ctx)
    export_id = uuid4()
    storage = ctx.service.storage
    count, length, digest = 0, 0, hashlib.sha256()
    with tempfile.TemporaryFile(mode="w+b") as file:
        for row in roster(ctx, body):
            line = (
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode()
            count += 1
            length += len(line)
            if count > MAX_ROWS or length > MAX_BYTES:
                raise APIError(422, "REPORT_TOO_LARGE")
            digest.update(line)
            file.write(line)
        file.seek(0)
        key = f"tenants/{ctx.tenant['id']}/exports/{export_id}"
        content, client = None, None
        try:
            if storage.mapping:
                destination, key = export_location(storage, ctx.tenant, export_id)
                storage.client(ctx.tenant)
                with storage.locks[str(ctx.tenant["id"])]:
                    session_expires, client = storage.sessions[
                        (storage.role_arn, str(ctx.tenant["id"]))
                    ]
                ctx.uploaded_exports.append(export_id)
                client.put_object(
                    Bucket=destination["bucket"],
                    Key=key,
                    Body=file,
                    ContentLength=length,
                    ContentType="application/x-ndjson",
                    CacheControl="no-store",
                    IfNoneMatch="*",
                    ServerSideEncryption="aws:kms",
                    SSEKMSKeyId=destination["kms_key_id"],
                    BucketKeyEnabled=True,
                    Metadata={"sha256": digest.hexdigest()},
                )
                # Cap by the very session attached to the uploading/signing client.
                deadline = min(deadline, session_expires)
            else:
                content = file.read()
            now = wall_now()
            deadline = min(
                deadline, authority_deadline(ctx), now + timedelta(seconds=60)
            )
            seconds = math.floor((deadline - now).total_seconds())
            if seconds < 1:
                raise APIError(401, "UNAUTHENTICATED")
            if client is not None:
                url = client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": destination["bucket"], "Key": key},
                    ExpiresIn=seconds,
                    HttpMethod="GET",
                )
                query = parse_qs(urlsplit(url).query)
                signed_at = datetime.strptime(
                    query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=UTC)
                expires_at = signed_at + timedelta(
                    seconds=int(query["X-Amz-Expires"][0])
                )
                if expires_at > deadline or expires_at <= wall_now():
                    raise APIError(503, "SERVICE_UNAVAILABLE")
            else:
                expires = int(now.timestamp()) + seconds
                expires_at = datetime.fromtimestamp(expires, UTC)
                token = signature(
                    ctx.service.export_secret, ctx.tenant["id"], export_id, expires
                )
                url = (
                    str(request.url_for("download_export", export_id=export_id))
                    + f"?expires={expires}&signature={token}"
                )
        except (BotoCoreError, ClientError, KeyError, ValueError) as exc:
            raise APIError(503, "SERVICE_UNAVAILABLE") from exc
    metadata = {
        "id": export_id.hex,
        "report": body.report,
        "row_count": count,
        "byte_length": length,
        "sha256": digest.hexdigest(),
        "created_at": now,
        "expires_at": expires_at,
    }
    filters = {
        "as_of": (body.as_of or ctx.now.date()).isoformat(),
        "include_compensation": body.include_compensation,
    }
    run(
        ctx.conn,
        """INSERT INTO report_exports VALUES
        (:id,:tid,:account,:client,:grant,:report,CAST(:filters AS jsonb),:row_count,:byte_length,:sha256,:key,:content,:created_at,:expires_at)""",
        **(metadata | {"id": export_id}),
        account=ctx.p.account_id,
        client=ctx.p.client_id,
        grant=ctx.p.grant_id,
        filters=json.dumps(filters),
        key=key,
        content=content,
    )
    audit.object_record(
        ctx.conn,
        ctx.p,
        ctx.request_id,
        "report_exports",
        export_id,
        "created",
        None,
        metadata | {"filters": filters, "grant_id": ctx.p.grant_id},
        ctx.now,
    )
    request.state.identity_context = {"export_id": export_id.hex}
    return metadata | {"download_url": url}


def prune_local(db, tenant_id):
    # Keep disclosure metadata/audit; only the local snapshot body has a one-day backstop.
    with db.tenant_tx(tenant_id, owner=True) as conn:
        return run(
            conn,
            """UPDATE report_exports SET content=NULL WHERE tenant_id=:tid
            AND content IS NOT NULL AND created_at<:cutoff""",
            cutoff=wall_now() - timedelta(days=1),
        ).rowcount


def main():
    import argparse
    from uuid import UUID

    from .config import APP_URL, OWNER_URL
    from .db import Database

    parser = argparse.ArgumentParser(
        description="Owner-only removal of local report bodies older than one day"
    )
    parser.add_argument("--tenant", required=True, type=UUID)
    args = parser.parse_args()
    db = Database(APP_URL, OWNER_URL)
    try:
        print(
            json.dumps(
                {
                    "tenant_id": str(args.tenant),
                    "pruned_bodies": prune_local(db, args.tenant),
                }
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
