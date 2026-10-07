import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import boto3
from botocore.stub import ANY, Stubber
from test_m3_identity import ambient, assertion, exchange, grant, setup_asu

from mock_workday import reports
from mock_workday.db import one, run
from mock_workday.errors import APIError
from mock_workday.ids import seed_id
from mock_workday.storage import SDK_CONFIG, Storage


def export(env, token, **body):
    return env.client.post(
        "/api/v1/report-exports",
        headers=env.headers(token),
        json={"report": "worker-roster", **body},
    )


def contents(env, response):
    assert response.status_code == 201, response.text
    download = env.client.get(response.json()["download_url"])
    assert download.status_code == 200, download.text
    return [json.loads(line) for line in download.text.splitlines()]


def test_T_M3_R_01_current_row_field_security_scopes_and_operations(env):
    response = export(env, env.login("bob"), include_compensation=True)
    result = contents(env, response)
    assert all("content" not in r for r in result)
    assert {r["id"] for r in result} == {
        r["id"]
        for r in env.get("/api/v1/workers?limit=200", env.login("bob")).json()["data"]
    }
    assert {r["id"] for r in result if "compensation" in r} == {
        env.id("workers", "Bob")
    }
    assert export(env, env.delegated("bob", scopes=["absence"])).status_code == 403
    assert (
        export(
            env, env.delegated("bob", scopes=["staffing"]), include_compensation=True
        ).status_code
        == 403
    )
    asu, _ = setup_asu(env, operations=["list_workers_api_v1_workers_get"])
    _, g = grant(env, asu)
    assert export(env, exchange(env, asu, g).json()["access_token"]).status_code == 403
    asu, key = setup_asu(
        env, mode="AMBIENT", operations=["create_export_api_v1_report_exports_post"]
    )
    ambient_token = ambient(env, asu, assertion(env, asu, key)).json()["access_token"]
    assert export(env, ambient_token).status_code == 201
    assert export(env, env.login("bob"), report="arbitrary-sql").status_code == 422
    assert export(env, env.login("bob"), document_body=True).status_code == 422


def test_T_M3_R_01_snapshot_historical_rows_and_limits(env, monkeypatch):
    original = reports.roster
    changed = False
    tid = seed_id("acme", "tenant", "acme")

    def concurrent_change(ctx, body):
        nonlocal changed
        assert (
            one(ctx.conn, "SHOW transaction_isolation")["transaction_isolation"]
            == "repeatable read"
        )
        for item in original(ctx, body):
            if not changed:
                with env.db.tenant_tx(tid, owner=True) as conn:
                    run(
                        conn,
                        "UPDATE workers SET name='Changed concurrently' WHERE tenant_id=:tid",
                    )
                changed = True
            yield item

    monkeypatch.setattr(reports, "roster", concurrent_change)
    result = contents(env, export(env, env.login("alice"), as_of="2026-10-01"))
    assert all(r["descriptor"] != "Changed concurrently" for r in result)
    monkeypatch.setattr(reports, "MAX_ROWS", 1)
    assert export(env, env.login("alice")).status_code == 422
    monkeypatch.setattr(reports, "MAX_ROWS", 10000)
    monkeypatch.setattr(reports, "MAX_BYTES", 1)
    assert export(env, env.login("alice")).status_code == 422
    with env.db.tenant_tx(tid) as conn:
        assert (
            one(conn, "SELECT count(*) AS n FROM report_exports WHERE tenant_id=:tid")[
                "n"
            ]
            == 1
        )


def test_T_M3_R_02_local_capability_reuse_revocation_expiry_and_tenant_binding(
    env, monkeypatch, caplog
):
    caplog.set_level("INFO", logger="mock_workday.requests")
    token = env.delegated("bob", scopes=["staffing"])
    response = export(env, token)
    data = response.json()
    contents(env, response)
    assert response.headers["cache-control"] == "no-store"
    expires = datetime.fromisoformat(data["expires_at"])
    assert 57 <= (expires - reports.wall_now()).total_seconds() <= 60
    tid = seed_id("acme", "tenant", "acme")
    with env.db.tenant_tx(tid, owner=True) as conn:
        run(
            conn,
            "UPDATE delegation_grants SET revoked_at=:now WHERE tenant_id=:tid",
            now=env.service.clock.now(),
        )
    assert export(env, token).status_code == 401
    assert env.client.get(data["download_url"]).status_code == 200
    assert (
        env.client.get(
            data["download_url"], headers={"Host": "globex.mockworkday.local"}
        ).status_code
        == 404
    )
    parsed = urlsplit(data["download_url"])
    altered = (
        parsed.path.replace(str(UUID(data["id"])), str(uuid4())) + "?" + parsed.query
    )
    assert env.client.get(altered).status_code == 404
    monkeypatch.setattr(reports, "wall_now", lambda: expires)
    assert env.client.get(data["download_url"]).status_code == 404
    assert data["download_url"] not in caplog.text
    assert parse_qs(parsed.query)["signature"][0] not in caplog.text
    with env.db.tenant_tx(tid) as conn:
        item = one(
            conn,
            "SELECT * FROM report_exports WHERE tenant_id=:tid AND id=:id",
            id=UUID(data["id"]),
        )
        audit = one(
            conn,
            "SELECT new_value FROM audit_objects WHERE tenant_id=:tid AND object_id=:id",
            id=UUID(data["id"]),
        )
        assert "download_url" not in item and "download_url" not in audit["new_value"]
        assert audit["new_value"]["grant_id"] is not None


def aws_export(env, monkeypatch, *, session_seconds=900):
    tid = seed_id("acme", "tenant", "acme")
    storage = Storage(
        {str(tid): {"bucket": "synthetic-exports", "kms_key_id": "synthetic-key"}},
        "role",
    )
    client = boto3.client(
        "s3",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        config=SDK_CONFIG.merge(
            __import__("botocore.config", fromlist=["Config"]).Config(
                signature_version="s3v4"
            )
        ),
    )
    storage.sessions[("role", str(tid))] = (
        reports.wall_now() + timedelta(seconds=session_seconds),
        client,
    )
    monkeypatch.setattr(storage, "client", lambda tenant: client)
    env.service.storage = storage
    return storage, client, tid


def test_T_M3_R_02_aws_encryption_caps_audit_and_upload_failure(env, monkeypatch):
    _storage, client, tid = aws_export(env, monkeypatch, session_seconds=20)
    token = env.login("alice")
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "synthetic-exports",
                "Key": ANY,
                "Body": ANY,
                "ContentLength": ANY,
                "ContentType": "application/x-ndjson",
                "CacheControl": "no-store",
                "IfNoneMatch": "*",
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": "synthetic-key",
                "BucketKeyEnabled": True,
                "Metadata": ANY,
            },
        )
        response = export(env, token)
        assert response.status_code == 201, response.text
        data = response.json()
        query = parse_qs(urlsplit(data["download_url"]).query)
        assert 1 <= int(query["X-Amz-Expires"][0]) <= 20
        assert urlsplit(data["download_url"]).scheme == "https"
        expected_expiry = datetime.strptime(
            query["X-Amz-Date"][0], "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=UTC) + timedelta(seconds=int(query["X-Amz-Expires"][0]))
        assert datetime.fromisoformat(data["expires_at"]) == expected_expiry
        stub.add_client_error("put_object", "AccessDenied")
        stub.add_response("delete_object", {})
        assert export(env, token).status_code == 503
        stub.add_response("put_object", {})
        stub.add_response("delete_object", {})
        monkeypatch.setattr(
            reports.audit,
            "object_record",
            lambda *a, **k: (_ for _ in ()).throw(APIError(503, "AUDIT_UNAVAILABLE")),
        )
        failed = export(env, token)
        assert failed.status_code == 503 and "download_url" not in failed.text
        stub.assert_no_pending_responses()
    with env.db.tenant_tx(tid) as conn:
        assert (
            one(conn, "SELECT count(*) AS n FROM report_exports WHERE tenant_id=:tid")[
                "n"
            ]
            == 1
        )
        assert (
            one(conn, "SELECT content FROM report_exports WHERE tenant_id=:tid")[
                "content"
            ]
            is None
        )


def test_T_M3_R_02_grant_and_credential_caps_and_no_local_audit_leak(env, monkeypatch):
    asu, _ = setup_asu(env)
    _, g = grant(env, asu)
    token = exchange(env, asu, g).json()["access_token"]
    tid = seed_id("acme", "tenant", "acme")
    with env.db.tenant_tx(tid, owner=True) as conn:
        run(
            conn,
            "UPDATE credential_versions SET accept_until=:until WHERE tenant_id=:tid",
            until=env.service.clock.now() + timedelta(seconds=10),
        )
    response = export(env, token)
    assert response.status_code == 201, response.text
    assert (
        7
        <= (
            datetime.fromisoformat(response.json()["expires_at"]) - reports.wall_now()
        ).total_seconds()
        <= 10
    )
    with env.db.tenant_tx(tid, owner=True) as conn:
        run(
            conn,
            "UPDATE delegation_grants SET expires_at=:until WHERE tenant_id=:tid",
            until=env.service.clock.now() + timedelta(seconds=5),
        )
    response = export(env, token)
    assert response.status_code == 201, response.text
    assert (
        2
        <= (
            datetime.fromisoformat(response.json()["expires_at"]) - reports.wall_now()
        ).total_seconds()
        <= 5
    )
    monkeypatch.setattr(
        reports.audit,
        "object_record",
        lambda *a, **k: (_ for _ in ()).throw(APIError(503, "AUDIT_UNAVAILABLE")),
    )
    assert export(env, token).status_code == 503
    with env.db.tenant_tx(tid) as conn:
        assert (
            one(conn, "SELECT count(*) AS n FROM report_exports WHERE tenant_id=:tid")[
                "n"
            ]
            == 2
        )


def test_T_M3_R_01_hidden_rows_and_R_02_token_expiry_cleanup(env, monkeypatch):
    tid = seed_id("acme", "tenant", "acme")
    with env.db.tenant_tx(tid, owner=True) as conn:
        run(
            conn,
            "DELETE FROM domain_grants WHERE tenant_id=:tid AND domain='WORKER_BASIC' AND group_id<>:self",
            self=seed_id("acme", "security_groups", "Employee as Self"),
        )
    token = env.login("bob")
    env.service.clock.advance(292)
    response = export(env, token)
    data = contents(env, response)
    assert [row["id"] for row in data] == [env.id("workers", "Bob")]
    expires = datetime.fromisoformat(response.json()["expires_at"])
    assert 5 <= (expires - reports.wall_now()).total_seconds() <= 8
    now = reports.wall_now()
    monkeypatch.setattr(reports, "wall_now", lambda: now + timedelta(days=2))
    assert reports.prune_local(env.db, tid) == 1
    with env.db.tenant_tx(tid) as conn:
        assert (
            one(conn, "SELECT content FROM report_exports WHERE tenant_id=:tid")[
                "content"
            ]
            is None
        )
    assert env.client.get(response.json()["download_url"]).status_code == 404


def test_T_M3_R_02_aws_lost_response_keeps_committed_snapshot(env, monkeypatch):
    _storage, client, tid = aws_export(env, monkeypatch)
    token = env.login("bob")
    assert (
        env.admin.post(
            "/admin/faults",
            json={
                "slug": "acme",
                "type": "timeout_after_commit",
                "count": 1,
                "match": {"method": "POST", "path_prefix": "/api/v1/report-exports"},
            },
        ).status_code
        == 201
    )
    with Stubber(client) as stub:
        stub.add_response("put_object", {})
        assert export(env, token).status_code == 504
        stub.assert_no_pending_responses()
    with env.db.tenant_tx(tid) as conn:
        assert (
            one(conn, "SELECT count(*) AS n FROM report_exports WHERE tenant_id=:tid")[
                "n"
            ]
            == 1
        )
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM audit_objects WHERE tenant_id=:tid AND object_type='report_exports'",
            )["n"]
            == 1
        )
