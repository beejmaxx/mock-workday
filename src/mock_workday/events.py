import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .db import one, rows, run
from .storage import SDK_CONFIG

SOURCE = "lab.mock-workday"
DETAIL_TYPE = "MockWorkday.BusinessEvent.v1"
logger = logging.getLogger(__name__)


class BusinessEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    event_id: UUID
    tenant_id: UUID
    tenant_slug: str
    event_type: Literal[
        "job_change.submitted", "job_change.approved", "time_off.approved"
    ]
    occurred_at: datetime
    business_process_event_id: UUID
    business_process_version: int = Field(ge=1, strict=True)
    status: Literal["IN_PROGRESS", "SUCCESSFULLY_COMPLETED"]
    subject_worker_id: UUID
    effective_date: date | None
    by_user_id: UUID
    on_behalf_of_user_id: UUID | None
    client_id: str | None
    request_id: str
    resource_href: str


def wall_now():
    return datetime.now(UTC)


def detail_for(tenant, event, history):
    if not history["request_id"] or history["business_process_version"] is None:
        return None  # Historical seeds were never external notifications.
    if event["type"] == "CHANGE_JOB" and history["action"] == "INITIATE":
        event_type = "job_change.submitted"
    elif (
        history["action"] == "APPROVE"
        and history["resulting_status"] == "SUCCESSFULLY_COMPLETED"
    ):
        event_type = (
            "job_change.approved"
            if event["type"] == "CHANGE_JOB"
            else "time_off.approved"
        )
    else:
        return None
    return {
        "schema_version": 1,
        "event_id": str(history["id"]),
        "tenant_id": str(tenant["id"]),
        "tenant_slug": tenant["slug"],
        "event_type": event_type,
        "occurred_at": history["at"].isoformat(),
        "business_process_event_id": str(event["id"]),
        "business_process_version": history["business_process_version"],
        "status": history["resulting_status"],
        "subject_worker_id": str(event["subject_worker_id"]),
        "effective_date": event["effective_date"].isoformat()
        if event["effective_date"]
        else None,
        "by_user_id": str(history["by_user_account_id"] or history["actor_account_id"]),
        "on_behalf_of_user_id": str(history["on_behalf_of_user_account_id"])
        if history["on_behalf_of_user_account_id"]
        else None,
        "client_id": history["actor_client_id"],
        "request_id": history["request_id"],
        "resource_href": f"/api/v1/business-process-events/{event['id'].hex}",
    }


def enqueue(conn, detail):
    if detail is None:
        return
    now = wall_now()
    run(
        conn,
        """INSERT INTO event_outbox (tenant_id,event_id,schema_version,detail,created_at,next_attempt_at)
        VALUES (:tid,:id,1,CAST(:detail AS jsonb),:now,:now)""",
        id=UUID(detail["event_id"]),
        detail=json.dumps(detail),
        now=now,
    )


def publish(client, bus, details, capture):
    if not bus:
        capture.extend(details)
        return [{"EventId": d["event_id"]} for d in details]
    try:
        response = client.put_events(
            Entries=[
                {
                    "Source": SOURCE,
                    "DetailType": DETAIL_TYPE,
                    "EventBusName": bus,
                    "Detail": json.dumps(detail, separators=(",", ":")),
                }
                for detail in details
            ]
        )
        entries = response.get("Entries", [])
        # An incomplete/unknown result must never acknowledge an unconfirmed entry.
        return [
            entries[i] if i < len(entries) and isinstance(entries[i], dict) else {}
            for i in range(len(details))
        ]
    except (BotoCoreError, ClientError):
        return [{} for _ in details]


def dispatch_tenant(db, tenant_id, client, bus, capture):
    with db.tenant_tx(tenant_id) as conn:
        now = wall_now()
        batch = rows(
            conn,
            """SELECT * FROM event_outbox WHERE tenant_id=:tid
            AND published_at IS NULL AND next_attempt_at<=:now
            ORDER BY next_attempt_at,event_id LIMIT 10 FOR UPDATE SKIP LOCKED""",
            now=now,
        )
        if not batch:
            return 0
        results = publish(client, bus, [row["detail"] for row in batch], capture)
        finished = wall_now()
        for row, result in zip(batch, results, strict=True):
            accepted = bool(result.get("EventId")) and not result.get("ErrorCode")
            attempts = row["attempt_count"] + 1
            run(
                conn,
                """UPDATE event_outbox SET attempt_count=:attempts,
                next_attempt_at=:next,published_at=:published,last_error=:error
                WHERE tenant_id=:tid AND event_id=:id""",
                id=row["event_id"],
                attempts=attempts,
                next=finished + timedelta(seconds=min(2 ** min(attempts - 1, 6), 60)),
                published=finished if accepted else None,
                error=None if accepted else "PUBLISH_FAILED_OR_UNKNOWN",
            )
        pending = one(
            conn,
            """SELECT count(*) AS count,min(created_at) AS oldest FROM event_outbox
            WHERE tenant_id=:tid AND published_at IS NULL""",
        )
        if pending["count"]:
            logger.warning(
                json.dumps(
                    {
                        "event": "outbox_pending",
                        "tenant_id": str(tenant_id),
                        "unpublished_count": pending["count"],
                        "oldest_age_seconds": max(
                            0, int((finished - pending["oldest"]).total_seconds())
                        ),
                    }
                )
            )
        return len(batch)


def dispatch_once(service, client=None, bus=None):
    with service.db.app.connect() as conn:
        tenants = rows(conn, "SELECT id FROM tenants WHERE enabled ORDER BY id")
    attempted = 0
    for tenant in tenants:
        attempted += dispatch_tenant(
            service.db, tenant["id"], client, bus, service.event_capture
        )
    return attempted


def dispatcher(service, stop):
    bus = os.getenv("MW_EVENT_BUS_ARN")
    client = None
    while not stop.is_set():
        try:
            if bus and client is None:
                client = boto3.session.Session().client("events", config=SDK_CONFIG)
            dispatch_once(service, client, bus)
        except Exception as exc:  # noqa: BLE001 — the dispatcher must survive a failed pass
            # Exception messages and tracebacks can contain event details.
            logger.error(
                json.dumps(
                    {
                        "event": "outbox_dispatch_failed",
                        "exception_class": type(exc).__name__,
                    }
                )
            )
        stop.wait(1)


def republish(db, tenant_id, start, end, *, request_id):
    from .identity import identity_audit

    if (
        start.tzinfo is None
        or end.tzinfo is None
        or end <= start
        or end - start > timedelta(days=7)
    ):
        raise ValueError("Use an aware, increasing history range of at most seven days")
    with db.tenant_tx(tenant_id, owner=True) as conn:
        tenant = one(conn, "SELECT * FROM tenants WHERE id=:tid")
        if not tenant or not tenant["enabled"]:
            raise ValueError("Unknown or disabled tenant")
        history = rows(
            conn,
            """SELECT * FROM bp_history WHERE tenant_id=:tid AND at>=:start AND at<:end
            AND request_id IS NOT NULL AND (action='INITIATE' OR (action='APPROVE' AND resulting_status='SUCCESSFULLY_COMPLETED'))
            ORDER BY at,id LIMIT 101""",
            start=start,
            end=end,
        )
        if len(history) > 100:
            raise ValueError("Narrow the history range to at most 100 transitions")
        count = 0
        for h in history:
            event = one(
                conn,
                "SELECT * FROM bp_events WHERE tenant_id=:tid AND id=:id",
                id=h["event_id"],
            )
            detail = detail_for(tenant, event, h)
            if not detail:
                continue
            now = wall_now()
            run(
                conn,
                """INSERT INTO event_outbox (tenant_id,event_id,schema_version,detail,created_at,next_attempt_at)
                VALUES (:tid,:id,1,CAST(:detail AS jsonb),:now,:now)
                ON CONFLICT (tenant_id,event_id) DO UPDATE SET published_at=NULL,next_attempt_at=:now,last_error=NULL""",
                id=h["id"],
                detail=json.dumps(detail),
                now=now,
            )
            identity_audit(
                conn,
                "event_republish:" + str(h["id"]),
                now,
                request_id,
                operator="owner",
            )
            count += 1
        return count


def prune(db, tenant_id):
    with db.tenant_tx(tenant_id, owner=True) as conn:
        return run(
            conn,
            "DELETE FROM event_outbox WHERE tenant_id=:tid AND published_at<:cutoff",
            cutoff=wall_now() - timedelta(days=7),
        ).rowcount


def redrive(db, tenant_id, sqs, client, bus, queue_url, *, request_id):
    from .identity import identity_audit

    # Owner IAM also limits Receive/Delete to provider DLQs. Never poll a consumer queue.
    parts = bus.split(":", 5)
    if (
        len(parts) != 6
        or parts[:4] != ["arn", "aws", "events", "us-east-2"]
        or not parts[5].startswith("event-bus/")
    ):
        raise ValueError("Use the provisioned Ohio event bus ARN")
    account = parts[4]
    queue = urlsplit(queue_url)
    if (
        queue.scheme != "https"
        or queue.netloc != "sqs.us-east-2.amazonaws.com"
        or queue.query
        or queue.fragment
        or not queue.path.startswith(f"/{account}/")
    ):
        raise ValueError("DLQ must belong to the Mock Workday account and Region")
    messages = sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=0,
        VisibilityTimeout=60,
        MessageAttributeNames=["All"],
    ).get("Messages", [])
    accepted = 0
    for message in messages:
        try:
            envelope = json.loads(message["Body"])
            detail = envelope["detail"]
            BusinessEvent.model_validate(detail)
            if (
                envelope.get("source") != SOURCE
                or envelope.get("detail-type") != DETAIL_TYPE
                or envelope.get("account") != account
                or UUID(detail["tenant_id"]) != tenant_id
            ):
                continue
        except (ValueError, KeyError, TypeError, ValidationError):
            continue
        with db.tenant_tx(tenant_id, owner=True) as conn:
            # Redrive only known committed intent, not caller-crafted event content.
            h = one(
                conn,
                "SELECT * FROM bp_history WHERE tenant_id=:tid AND id=:id",
                id=UUID(detail["event_id"]),
            )
            tenant = one(conn, "SELECT * FROM tenants WHERE id=:tid")
            if not h or not tenant or not tenant["enabled"]:
                continue
            event = one(
                conn,
                "SELECT * FROM bp_events WHERE tenant_id=:tid AND id=:id",
                id=h["event_id"],
            )
            if json.dumps(detail_for(tenant, event, h), sort_keys=True) != json.dumps(
                detail, sort_keys=True
            ):
                continue
            identity_audit(
                conn,
                "event_redrive:" + detail["event_id"],
                wall_now(),
                request_id,
                operator="owner",
            )
        result = publish(client, bus, [detail], None)[0]
        if result.get("EventId") and not result.get("ErrorCode"):
            sqs.delete_message(
                QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"]
            )
            accepted += 1
    return accepted


def main():
    import argparse
    from uuid import uuid4

    from .config import APP_URL, OWNER_URL
    from .db import Database

    parser = argparse.ArgumentParser(
        description="Owner-only bounded outbox repair/retention"
    )
    parser.add_argument("--tenant", required=True, type=UUID)
    sub = parser.add_subparsers(dest="command", required=True)
    repair = sub.add_parser("republish")
    repair.add_argument("--start", required=True, type=datetime.fromisoformat)
    repair.add_argument("--end", required=True, type=datetime.fromisoformat)
    sub.add_parser("prune")
    dlq = sub.add_parser("redrive")
    dlq.add_argument("--queue-url", required=True)
    args = parser.parse_args()
    db = Database(APP_URL, OWNER_URL)
    try:
        if args.command == "redrive":
            bus = os.environ["MW_EVENT_BUS_ARN"]
            session = boto3.session.Session()
            count = redrive(
                db,
                args.tenant,
                session.client("sqs", config=SDK_CONFIG),
                session.client("events", config=SDK_CONFIG),
                bus,
                args.queue_url,
                request_id="owner-" + uuid4().hex,
            )
        elif args.command == "republish":
            count = republish(
                db, args.tenant, args.start, args.end, request_id="owner-" + uuid4().hex
            )
        else:
            count = prune(db, args.tenant)

        print(
            json.dumps(
                {
                    "operation": args.command,
                    "tenant_id": str(args.tenant),
                    "count": count,
                }
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
