import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.stub import ANY, Stubber
from sqlalchemy.exc import SQLAlchemyError
from test_m2 import action, approved, change, complete, created, time_off

from mock_workday import events
from mock_workday.db import one, rows, run
from mock_workday.ids import seed_id

BUS = "arn:aws:events:us-east-2:123456789012:event-bus/mock-workday"


def outbox(env, slug="acme"):
    with env.db.tenant_tx(seed_id(slug, "tenant", slug)) as conn:
        return rows(
            conn,
            "SELECT * FROM event_outbox WHERE tenant_id=:tid ORDER BY created_at,event_id",
        )


def aws_client(service):
    return boto3.client(
        service,
        region_name="us-east-2",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )


def test_T_M3_E_01_commit_only_types_schema_actor_and_redaction(env):
    assert outbox(env) == []
    event = created(change(env))
    first = outbox(env)
    assert len(first) == 1
    event = approved(action(env, event, "priya"))
    assert len(outbox(env)) == 1
    event = approved(action(env, event, "connie"))
    approved(action(env, time_off(env), "alice"))
    notifications = outbox(env)
    assert [r["detail"]["event_type"] for r in notifications] == [
        "job_change.submitted",
        "job_change.approved",
        "time_off.approved",
    ]
    assert [r["detail"]["business_process_version"] for r in notifications] == [1, 3, 2]
    for row in notifications:
        value = events.BusinessEvent.model_validate(row["detail"])
        assert value.tenant_id == seed_id("acme", "tenant", "acme")
        assert value.event_id == row["event_id"]
        assert not any(
            word in json.dumps(row["detail"])
            for word in (
                "140000",
                "Holiday",
                "reviewed",
                "annual_salary",
                "client_secret",
            )
        )
        with env.db.tenant_tx(value.tenant_id) as conn:
            h = one(
                conn,
                "SELECT * FROM bp_history WHERE tenant_id=:tid AND id=:id",
                id=row["event_id"],
            )
            assert h["request_id"] == value.request_id
            assert h["business_process_version"] == value.business_process_version
    assert notifications[1]["detail"]["by_user_id"] == str(
        seed_id("acme", "accounts", "connie")
    )
    assert notifications[1]["detail"]["effective_date"] == "2026-11-01"
    assert outbox(env, "globex") == []
    # A notification grants no authority for HTTP refetch, even if its identifiers are known.
    href = notifications[1]["detail"]["resource_href"]
    assert env.client.get(href).status_code == 401
    foreign = env.login("eve", slug="globex")
    assert (
        env.client.get(
            href, headers={"Host": "globex.mockworkday.local", **env.headers(foreign)}
        ).status_code
        == 404
    )


def test_T_M3_E_01_02_rollback_idempotency_and_excluded_transitions(env):
    response = env.admin.post(
        "/admin/faults",
        json={
            "slug": "acme",
            "type": "timeout_before_commit",
            "match": {
                "method": "POST",
                "path_prefix": "/api/v1/business-processes/change-job",
            },
            "count": 1,
        },
    )
    assert response.status_code == 201
    key = uuid4().hex
    assert change(env, key=key).status_code == 504
    assert not outbox(env)
    event = created(change(env, key=key))
    again = created(change(env, key=key))
    assert again["id"] == event["id"] and len(outbox(env)) == 1
    approved(action(env, event, "priya", verb="deny"))
    assert len(outbox(env)) == 1
    leave = time_off(env)
    approved(action(env, leave, "bob", verb="cancel"))
    assert len(outbox(env)) == 1
    assert events.dispatch_once(env.service) == 1
    assert len(env.service.event_capture) == 1
    assert events.dispatch_once(env.service) == 0


def test_T_M3_E_02_outbox_failure_rolls_back_transition_and_audit(env, monkeypatch):
    event = created(change(env))
    event = approved(action(env, event, "priya"))
    original = events.enqueue
    monkeypatch.setattr(
        events,
        "enqueue",
        lambda *a: (_ for _ in ()).throw(SQLAlchemyError("synthetic outbox failure")),
    )
    assert action(env, event, "connie").status_code == 503
    assert len(outbox(env)) == 1
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        assert (
            one(
                conn,
                "SELECT version FROM bp_events WHERE tenant_id=:tid AND id=:id",
                id=UUID(event["id"]),
            )["version"]
            == 2
        )
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM job_revisions WHERE tenant_id=:tid AND bp_event_id=:id",
                id=UUID(event["id"]),
            )["n"]
            == 0
        )
    monkeypatch.setattr(events, "enqueue", original)
    assert action(env, event, "connie").status_code == 200
    assert len(outbox(env)) == 2


def test_T_M3_E_02_partial_putevents_backoff_and_unknown_retry(env, monkeypatch):
    complete(env)
    client = aws_client("events")
    clock = events.wall_now()
    monkeypatch.setattr(events, "wall_now", lambda: clock)
    original_ids = [r["event_id"] for r in outbox(env)]
    with Stubber(client) as stub:
        stub.add_response(
            "put_events",
            {
                "FailedEntryCount": 1,
                "Entries": [
                    {"EventId": str(uuid4())},
                    {"ErrorCode": "InternalFailure", "ErrorMessage": "synthetic"},
                ],
            },
            {"Entries": [ANY, ANY]},
        )
        assert events.dispatch_once(env.service, client, BUS) == 2
        assert events.dispatch_once(env.service, client, BUS) == 0
        pending = [r for r in outbox(env) if r["published_at"] is None]
        assert len(pending) == 1 and pending[0]["attempt_count"] == 1
        assert pending[0]["next_attempt_at"] == clock + timedelta(seconds=1)
        clock += timedelta(seconds=1)
        stub.add_client_error("put_events", "InternalFailure")
        assert events.dispatch_once(env.service, client, BUS) == 1
        pending = next(r for r in outbox(env) if r["published_at"] is None)
        assert pending["attempt_count"] == 2 and pending[
            "next_attempt_at"
        ] == clock + timedelta(seconds=2)
        clock += timedelta(seconds=2)
        stub.add_response(
            "put_events",
            {"FailedEntryCount": 0, "Entries": [{"EventId": str(uuid4())}]},
        )
        assert events.dispatch_once(env.service, client, BUS) == 1
        stub.assert_no_pending_responses()
    assert all(r["published_at"] for r in outbox(env))
    assert [r["event_id"] for r in outbox(env)] == original_ids


def test_T_M3_E_02_crash_after_acceptance_restart_and_locked_dispatch(env, monkeypatch):
    created(change(env))
    original = events.run
    captured = []

    def fail_mark(conn, sql, **params):
        if sql.startswith("UPDATE event_outbox"):
            raise SQLAlchemyError("crash before commit")
        return original(conn, sql, **params)

    monkeypatch.setattr(events, "run", fail_mark)
    tid = seed_id("acme", "tenant", "acme")
    with pytest.raises(SQLAlchemyError):
        events.dispatch_tenant(env.db, tid, None, None, captured)
    assert len(captured) == 1 and outbox(env)[0]["published_at"] is None
    monkeypatch.setattr(events, "run", original)
    barrier = Barrier(2)

    def dispatch():
        barrier.wait()
        return events.dispatch_tenant(env.db, tid, None, None, captured)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: dispatch(), range(2))) == [0, 1]
    assert captured[0] == captured[1]
    assert outbox(env)[0]["attempt_count"] == 1


def test_T_M3_E_02_timeout_cap_repair_and_immutable_privileges(env, monkeypatch):
    complete(env)
    tid = seed_id("acme", "tenant", "acme")
    original = [r["detail"] for r in outbox(env)]
    client = aws_client("events")
    monkeypatch.setattr(
        client,
        "put_events",
        lambda **kw: (_ for _ in ()).throw(
            EndpointConnectionError(endpoint_url="https://events.synthetic.invalid")
        ),
    )
    now = events.wall_now()
    with env.db.tenant_tx(tid, owner=True) as conn:
        run(conn, "UPDATE event_outbox SET attempt_count=8 WHERE tenant_id=:tid")
    monkeypatch.setattr(events, "wall_now", lambda: now)
    assert events.dispatch_once(env.service, client, BUS) == 2
    assert all(r["next_attempt_at"] == now + timedelta(seconds=60) for r in outbox(env))
    with pytest.raises(SQLAlchemyError), env.db.tenant_tx(tid) as conn:
        run(conn, "UPDATE event_outbox SET detail='{}' WHERE tenant_id=:tid")
    with env.db.tenant_tx(seed_id("globex", "tenant", "globex")) as conn:
        assert rows(conn, "SELECT * FROM event_outbox") == []
    start = env.service.clock.now() - timedelta(seconds=1)
    end = start + timedelta(seconds=2)
    assert events.republish(env.db, tid, start, end, request_id="repair-test") == 2
    assert [r["detail"] for r in outbox(env)] == original
    assert events.dispatch_once(env.service) == 2
    with env.db.tenant_tx(tid) as conn:
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM audit_identity WHERE tenant_id=:tid AND request_id='repair-test'",
            )["n"]
            == 2
        )
    with pytest.raises(ValueError):
        events.republish(
            env.db, tid, start, start + timedelta(days=8), request_id="too-wide"
        )
    now += timedelta(days=8)
    assert events.prune(env.db, tid) == 2
    assert not outbox(env)
    assert (
        events.republish(env.db, tid, start, end, request_id="restore-from-history")
        == 2
    )
    assert [
        r["detail"]
        for r in sorted(
            outbox(env), key=lambda r: r["detail"]["business_process_version"]
        )
    ] == original


def test_T_M3_E_04_owner_redrive_deletes_only_confirmed_known_events(env):
    created(change(env))
    detail = outbox(env)[0]["detail"]
    queue = "https://sqs.us-east-2.amazonaws.com/123456789012/provider-dlq"
    sqs, client = aws_client("sqs"), aws_client("events")
    envelope = {
        "source": events.SOURCE,
        "detail-type": events.DETAIL_TYPE,
        "account": "123456789012",
        "detail": detail,
    }
    tid = seed_id("acme", "tenant", "acme")
    with Stubber(sqs) as queue_stub, Stubber(client) as event_stub:
        queue_stub.add_response(
            "receive_message",
            {
                "Messages": [
                    {
                        "MessageId": "m",
                        "ReceiptHandle": "r",
                        "Body": json.dumps(envelope),
                    }
                ]
            },
        )
        event_stub.add_response(
            "put_events",
            {"FailedEntryCount": 1, "Entries": [{"ErrorCode": "InternalFailure"}]},
        )
        assert (
            events.redrive(env.db, tid, sqs, client, BUS, queue, request_id="redrive1")
            == 0
        )
        queue_stub.add_response(
            "receive_message",
            {
                "Messages": [
                    {
                        "MessageId": "m",
                        "ReceiptHandle": "r2",
                        "Body": json.dumps(envelope),
                    }
                ]
            },
        )
        event_stub.add_response(
            "put_events",
            {"FailedEntryCount": 0, "Entries": [{"EventId": str(uuid4())}]},
            {
                "Entries": [
                    {
                        "Source": events.SOURCE,
                        "DetailType": events.DETAIL_TYPE,
                        "EventBusName": BUS,
                        "Detail": json.dumps(detail, separators=(",", ":")),
                    }
                ]
            },
        )
        queue_stub.add_response(
            "delete_message", {}, {"QueueUrl": queue, "ReceiptHandle": "r2"}
        )
        assert (
            events.redrive(env.db, tid, sqs, client, BUS, queue, request_id="redrive2")
            == 1
        )
        queue_stub.assert_no_pending_responses()
        event_stub.assert_no_pending_responses()


def test_T_M3_E_01_03_delegate_attribution_duplicates_and_out_of_order(env):
    from test_m3_identity import exchange, grant, setup_asu

    asu, _ = setup_asu(env)
    _, g = grant(env, asu, user="alice")
    token = exchange(env, asu, g).json()["access_token"]
    approved(action(env, time_off(env), "alice", token=token))
    detail = outbox(env)[0]["detail"]
    assert detail["by_user_id"] == str(UUID(asu["account_id"]))
    assert detail["on_behalf_of_user_id"] == str(seed_id("acme", "accounts", "alice"))
    assert detail["client_id"] == asu["client_id"]
    complete(env)
    notifications = [r["detail"] for r in outbox(env)]
    arrivals = list(reversed(notifications)) + notifications
    assert len({(d["tenant_id"], d["event_id"]) for d in arrivals}) == len(
        notifications
    )
    job = [d for d in arrivals if d["event_type"].startswith("job_change")]
    assert job[0]["business_process_version"] > job[1]["business_process_version"]
    assert env.get(job[1]["resource_href"], env.login("alice")).json()["version"] == 3
    # Neither the event ID nor its tenant field is an HTTP credential.
    assert env.get(detail["resource_href"], detail["event_id"]).status_code == 401


def test_T_M3_E_02_repair_audit_failure_rolls_back_and_unpublished_never_pruned(
    env, monkeypatch
):
    from mock_workday.errors import APIError

    created(change(env))
    tid = seed_id("acme", "tenant", "acme")
    events.dispatch_once(env.service)
    before = outbox(env)[0]
    monkeypatch.setattr(
        "mock_workday.identity.identity_audit",
        lambda *a, **kw: (_ for _ in ()).throw(APIError(503, "AUDIT_UNAVAILABLE")),
    )
    with pytest.raises(APIError):
        events.republish(
            env.db,
            tid,
            env.service.clock.now() - timedelta(seconds=1),
            env.service.clock.now() + timedelta(seconds=1),
            request_id="failed-repair",
        )
    assert outbox(env)[0]["published_at"] == before["published_at"]
    with env.db.tenant_tx(tid, owner=True) as conn:
        run(
            conn,
            "UPDATE event_outbox SET published_at=NULL,created_at=:old WHERE tenant_id=:tid",
            old=events.wall_now() - timedelta(days=30),
        )
    assert events.prune(env.db, tid) == 0
    assert len(outbox(env)) == 1


def test_T_M3_E_04_redrive_rejects_foreign_and_modified_messages(env):
    created(change(env))
    detail = outbox(env)[0]["detail"]
    tid = seed_id("acme", "tenant", "acme")
    queue = "https://sqs.us-east-2.amazonaws.com/123456789012/provider-dlq"
    sqs, client = aws_client("sqs"), aws_client("events")
    envelope = {
        "source": events.SOURCE,
        "detail-type": events.DETAIL_TYPE,
        "account": "123456789012",
        "detail": detail,
    }
    messages = [
        {"Body": "not-json", "ReceiptHandle": "bad"},
        {
            "Body": json.dumps(envelope | {"account": "111111111111"}),
            "ReceiptHandle": "foreign",
        },
        {
            "Body": json.dumps(
                envelope | {"detail": detail | {"schema_version": True}}
            ),
            "ReceiptHandle": "changed",
        },
        {
            "Body": json.dumps(
                envelope | {"detail": detail | {"request_id": "modified"}}
            ),
            "ReceiptHandle": "modified",
        },
    ]
    with Stubber(sqs) as queue_stub, Stubber(client) as event_stub:
        queue_stub.add_response("receive_message", {"Messages": messages})
        assert (
            events.redrive(
                env.db, tid, sqs, client, BUS, queue, request_id="invalid-redrive"
            )
            == 0
        )
        with pytest.raises(ValueError):
            events.redrive(
                env.db,
                tid,
                sqs,
                client,
                BUS,
                queue.replace("123456789012", "111111111111"),
                request_id="foreign-queue",
            )
        queue_stub.assert_no_pending_responses()
        event_stub.assert_no_pending_responses()
