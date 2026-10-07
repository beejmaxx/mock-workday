import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import boto3
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from mock_workday.db import one, run
from mock_workday.errors import APIError
from mock_workday.ids import seed_id
from mock_workday.storage import Storage, fingerprint


def aws_storage(env, monkeypatch):
    tid = seed_id("acme", "tenant", "acme")
    s3 = boto3.client(
        "s3",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    storage = Storage(
        {str(tid): {"bucket": "tenant-bucket", "kms_key_id": "tenant-key"}}, "role"
    )
    monkeypatch.setattr(storage, "client", lambda tenant: s3)
    env.service.storage = storage
    return storage, s3, {"id": tid, "enabled": True}


def document(env):
    return {
        "title": "Notes",
        "content": "Synthetic résumé",
        "domain": "DOC_WORKER",
        "classification": "CONFIDENTIAL",
        "owner_worker_id": env.id("workers", "Bob"),
    }


def test_T_M3_S_01_02_aws_api_integrity_authorization_and_metadata(env, monkeypatch):
    _storage, s3, tenant = aws_storage(env, monkeypatch)
    did = uuid4()
    monkeypatch.setattr("mock_workday.api.documents.uuid4", lambda: did)
    body = document(env)
    data = body["content"].encode()
    size, digest = fingerprint(body["content"])
    key = f"tenants/{tenant['id']}/documents/{did}"
    token = env.login("alice")
    with Stubber(s3) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "tenant-bucket",
                "Key": key,
                "Body": data,
                "ContentType": "text/plain; charset=utf-8",
                "IfNoneMatch": "*",
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": "tenant-key",
                "BucketKeyEnabled": True,
                "Metadata": {"sha256": digest},
            },
        )
        created = env.client.post(
            "/api/v1/documents", json=body, headers=env.headers(token)
        )
        assert created.status_code == 201, created.text
        assert "content" not in created.json()
        path = created.json()["href"]
        assert env.get(path, env.login("grace")).status_code == 404
        foreign = env.login("eve", slug="globex")
        assert (
            env.client.get(
                path,
                headers={"Host": "globex.mockworkday.local", **env.headers(foreign)},
            ).status_code
            == 404
        )
        assert env.get("/api/v1/documents", token).status_code == 200
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(data), len(data))},
            {"Bucket": "tenant-bucket", "Key": key},
        )
        assert env.get(path, token).json()["content"] == body["content"]
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"corrupt"), 7)},
            {"Bucket": "tenant-bucket", "Key": key},
        )
        assert env.get(path, token).status_code == 503
        stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
        assert env.get(path, token).status_code == 503
        stub.assert_no_pending_responses()
    with env.db.tenant_tx(tenant["id"]) as conn:
        row = one(
            conn, "SELECT * FROM documents WHERE tenant_id=:tid AND id=:id", id=did
        )
        assert row["content"] is None and row["content_bytes"] == size
        audit = one(
            conn,
            "SELECT * FROM audit_objects WHERE tenant_id=:tid AND object_id=:id",
            id=did,
        )
        assert "content" not in audit["new_value"]


def test_T_M3_S_02_rollback_cleanup_and_committed_timeout(env, monkeypatch):
    _storage, s3, tenant = aws_storage(env, monkeypatch)
    token = env.login("alice")
    did = uuid4()
    monkeypatch.setattr("mock_workday.api.documents.uuid4", lambda: did)
    with Stubber(s3) as stub:
        stub.add_response("put_object", {})
        stub.add_response("delete_object", {})
        original = __import__(
            "mock_workday.api.documents", fromlist=["object_record"]
        ).object_record
        monkeypatch.setattr(
            "mock_workday.api.documents.object_record",
            lambda *args: (_ for _ in ()).throw(APIError(503, "SERVICE_UNAVAILABLE")),
        )
        assert (
            env.client.post(
                "/api/v1/documents", json=document(env), headers=env.headers(token)
            ).status_code
            == 503
        )
        stub.assert_no_pending_responses()
        with env.db.tenant_tx(tenant["id"]) as conn:
            assert (
                one(
                    conn,
                    "SELECT id FROM documents WHERE tenant_id=:tid AND id=:id",
                    id=did,
                )
                is None
            )
        monkeypatch.setattr("mock_workday.api.documents.object_record", original)
        original_timeout = __import__(
            "mock_workday.service", fromlist=["timeout"]
        ).timeout

        def after(rules, phase):
            if phase == "timeout_after_commit":
                raise APIError(504, "TIMEOUT")
            return original_timeout(rules, phase)

        monkeypatch.setattr("mock_workday.service.timeout", after)
        stub.add_response("put_object", {})
        assert (
            env.client.post(
                "/api/v1/documents", json=document(env), headers=env.headers(token)
            ).status_code
            == 504
        )
        stub.assert_no_pending_responses()
        with env.db.tenant_tx(tenant["id"]) as conn:
            assert one(
                conn, "SELECT id FROM documents WHERE tenant_id=:tid AND id=:id", id=did
            )


def test_T_M3_S_02_failed_upload_and_reuse_are_fail_closed(env, monkeypatch):
    storage, s3, tenant = aws_storage(env, monkeypatch)
    with Stubber(s3) as stub:
        stub.add_client_error("put_object", "AccessDenied", http_status_code=403)
        with pytest.raises(APIError) as error:
            storage.put(tenant, uuid4(), "body")
        assert error.value.status == 503
        stub.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
        stub.add_response(
            "get_object", {"Body": StreamingBody(io.BytesIO(b"wrong"), 5)}
        )
        with pytest.raises(APIError):
            storage.put(tenant, uuid4(), "body", reuse=True)
        stub.assert_no_pending_responses()


def test_T_M3_ABAC_02_sessions_are_tenant_scoped_and_refresh_serialized(monkeypatch):
    tids = [uuid4(), uuid4()]
    storage = Storage(
        {str(t): {"bucket": f"bucket-{t}", "kms_key_id": str(t)} for t in tids}, "role"
    )
    expiration = datetime.now(UTC) + timedelta(seconds=900)
    storage.sts = Mock()
    storage.sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "testing",
            "SecretAccessKey": "testing",
            "SessionToken": "session",
            "Expiration": expiration,
        }
    }
    session = Mock()
    session.client.side_effect = [Mock(), Mock(), Mock()]
    monkeypatch.setattr(
        "mock_workday.storage.boto3.session.Session", lambda **kwargs: session
    )
    tenant = {"id": tids[0], "enabled": True}
    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: storage.client(tenant), range(16)))
    assert all(c is clients[0] for c in clients)
    assert storage.sts.assume_role.call_count == 1
    other = storage.client({"id": tids[1], "enabled": True})
    assert other is not clients[0]
    assert [call.kwargs["Tags"] for call in storage.sts.assume_role.call_args_list] == [
        [{"Key": "tenant", "Value": str(t)}] for t in tids
    ]
    assert all(
        call.kwargs["DurationSeconds"] == 900
        for call in storage.sts.assume_role.call_args_list
    )
    storage.sessions[("role", str(tids[0]))] = (
        datetime.now(UTC) + timedelta(seconds=119),
        clients[0],
    )
    assert storage.client(tenant) is not clients[0]
    for invalid in (
        {"id": tids[0], "enabled": False},
        {"id": uuid4(), "enabled": True},
    ):
        with pytest.raises(APIError):
            storage.client(invalid)
    assert storage.sts.assume_role.call_count == 3


def test_T_M3_BAL_01_current_authority_snapshots_and_owner_writes(env):
    tid = seed_id("acme", "tenant", "acme")
    wid = seed_id("acme", "workers", "Bob")
    with env.db.tenant_tx(tid, owner=True) as conn:
        for day, taken in (("2025-01-01", 0), ("2026-01-01", 8)):
            run(
                conn,
                """INSERT INTO time_off_balances VALUES (:id,:tid,:wid,'VACATION',:day,160,:taken,:left)""",
                id=uuid4(),
                wid=wid,
                day=day,
                taken=taken,
                left=160 - taken,
            )
    path = env.worker("Bob", "/time-off-balances")
    bob = env.login("bob")
    assert env.get(path, bob).json()["data"][0]["remainingHours"] == 152
    assert (
        env.get(path + "?as_of=2025-05-01", bob).json()["data"][0]["asOf"]
        == "2025-01-01"
    )
    assert env.get(path + "?as_of=2024-01-01", bob).json()["data"] == []
    assert env.get(path, env.login("grace")).status_code == 404
    delegated = env.delegated("bob", scopes=["staffing"])
    assert env.get(path, delegated).status_code == 404
    alice = env.login("alice")
    assert env.get(path, alice).status_code == 200
    initiated = env.client.post(
        "/api/v1/business-processes/request-time-off",
        headers={**env.headers(bob), "Idempotency-Key": uuid4().hex},
        json={
            "worker_id": wid.hex,
            "start_date": "2026-11-10",
            "end_date": "2026-11-10",
            "reason": "Synthetic day",
        },
    )
    assert initiated.status_code == 201, initiated.text
    event = initiated.json()["event"]
    approved = env.client.post(
        event["href"] + "/approve",
        headers={**env.headers(alice), "Idempotency-Key": uuid4().hex},
        json={
            "expected_step": "MANAGER_APPROVAL",
            "expected_version": event["version"],
        },
    )
    assert approved.status_code == 200, approved.text
    assert env.get(path, bob).json()["data"][0]["remainingHours"] == 152
    env.revoke_role("MANAGER", "SO-ENG", "P-ENG-DIR")
    assert env.get(path + "?as_of=2025-01-01", alice).status_code == 404
    with pytest.raises(Exception) as error, env.db.tenant_tx(tid) as conn:
        run(conn, "UPDATE time_off_balances SET taken_hours=0 WHERE tenant_id=:tid")
    assert "permission denied" in str(error.value)
    with env.db.tenant_tx(seed_id("globex", "tenant", "globex")) as conn:
        assert (
            one(conn, "SELECT id FROM time_off_balances WHERE worker_id=:wid", wid=wid)
            is None
        )


def test_T_M3_S_02_orphans_require_age_and_missing_metadata(env, monkeypatch):
    from mock_workday.storage import orphan_page

    storage, s3, tenant = aws_storage(env, monkeypatch)
    referenced = seed_id("acme", "documents", "Employee Handbook")
    orphan, young = uuid4(), uuid4()
    prefix = f"tenants/{tenant['id']}/documents/"
    now = datetime.now(UTC)
    listing = {
        "Contents": [
            {"Key": prefix + str(did), "LastModified": when, "Size": 4}
            for did, when in (
                (referenced, now - timedelta(days=2)),
                (orphan, now - timedelta(days=2)),
                (young, now),
            )
        ]
    }
    with Stubber(s3) as stub:
        stub.add_response("list_objects_v2", listing)
        stub.add_response(
            "delete_object",
            {},
            {"Bucket": "tenant-bucket", "Key": prefix + str(orphan)},
        )
        result = orphan_page(env.db, storage, tenant["id"], delete=True)
        assert result["orphan_keys"] == [prefix + str(orphan)]
        assert result["next_token"] is None
        stub.assert_no_pending_responses()


def test_T_M3_ABAC_02_refresh_failure_never_uses_old_session(monkeypatch):
    from botocore.exceptions import ClientError

    tid = uuid4()
    storage = Storage({str(tid): {"bucket": "bucket", "kms_key_id": "key"}}, "role")
    storage.sessions[("role", str(tid))] = (datetime.now(UTC), Mock())
    storage.sts = Mock()
    storage.sts.assume_role.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "AssumeRole"
    )
    with pytest.raises(APIError):
        storage.get({"id": tid, "enabled": True}, {"id": uuid4()})
    with pytest.raises(ValueError):
        Storage({str(tid): {"bucket": "bucket", "kms_key_id": "key"}})


def test_T_M3_S_02_cleanup_failure_is_correlated_and_best_effort(
    env, monkeypatch, caplog
):
    import json

    storage, s3, tenant = aws_storage(env, monkeypatch)
    did = uuid4()
    with Stubber(s3) as stub:
        stub.add_client_error("delete_object", "AccessDenied", http_status_code=403)
        storage.cleanup(tenant, did, request_id="cleanup-test")
        stub.assert_no_pending_responses()
    record = json.loads(caplog.records[-1].message)
    assert record == {
        "event": "document_orphan",
        "X-Request-Id": "cleanup-test",
        "tenant_id": str(tenant["id"]),
        "document_id": str(did),
    }


def test_T_M3_S_02_uncertain_commit_preserves_object(env, monkeypatch):
    from contextlib import contextmanager

    _storage, s3, tenant = aws_storage(env, monkeypatch)
    token = env.login("alice")
    did = uuid4()
    monkeypatch.setattr("mock_workday.api.documents.uuid4", lambda: did)
    original_tx = env.db.tenant_tx

    @contextmanager
    def lost_ack(tid, *, owner=False):
        with original_tx(tid, owner=owner) as conn:
            yield conn
        raise APIError(503, "SERVICE_UNAVAILABLE")

    monkeypatch.setattr(env.db, "tenant_tx", lost_ack)
    delete = Mock(wraps=s3.delete_object)
    monkeypatch.setattr(s3, "delete_object", delete)
    with Stubber(s3) as stub:
        stub.add_response("put_object", {})
        response = env.client.post(
            "/api/v1/documents", json=document(env), headers=env.headers(token)
        )
        assert response.status_code == 503
        delete.assert_not_called()
        stub.assert_no_pending_responses()
    with original_tx(tenant["id"]) as conn:
        assert one(
            conn, "SELECT id FROM documents WHERE tenant_id=:tid AND id=:id", id=did
        )
