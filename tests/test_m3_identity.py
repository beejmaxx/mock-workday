import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from mock_workday.clock import SEED_TIME
from mock_workday.db import one, rows, run
from mock_workday.identity import JWT_BEARER, OBO, OPERATIONS
from mock_workday.ids import seed_id


def certificate(*, bits=2048, expires=86400):
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic signer")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(SEED_TIME - timedelta(seconds=60))
        .not_valid_after(SEED_TIME + timedelta(seconds=expires))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


def setup_asu(env, mode="DELEGATE", *, operations=None, pem=None, ref=None):
    operations = sorted(OPERATIONS) if operations is None else operations
    r = env.admin.post(
        "/admin/agent-registrations",
        json={
            "slug": "acme",
            "ref_id": ref or uuid4().hex,
            "display_name": "Synthetic assistant",
        },
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    path = f"/admin/agent-registrations/{rid}"
    body = {
        "slug": "acme",
        "mode": mode,
        "scopes": ["staffing", "compensation", "absence", "documents"],
        "allowed_operations": operations,
    }
    if mode == "AMBIENT":
        body["group_ids"] = [env.id("security_groups", "Integration: Directory Reader")]
    r = env.admin.post(path + "/modes", json=body)
    assert r.status_code == 201, r.text
    asu = r.json() | {"registration_id": rid, "path": path, "operations": operations}
    assert not asu["enabled"]
    key = None
    if mode == "AMBIENT" and pem is None:
        key, pem = certificate()
    r = env.admin.post(
        path + f"/modes/{mode}/credentials",
        json={"slug": "acme", "certificate_pem": pem},
    )
    assert r.status_code == 201, r.text
    asu.update(r.json())
    assert (
        env.admin.patch(path, json={"slug": "acme", "enabled": True}).status_code == 200
    )
    assert (
        env.admin.patch(
            path + f"/modes/{mode}",
            json={
                "slug": "acme",
                "enabled": True,
                "scopes": body["scopes"],
                "allowed_operations": operations,
            },
        ).status_code
        == 200
    )
    return asu, key


def grant(env, asu, user="bob"):
    human = env.login(user)
    response = env.client.post(
        "/api/v1/delegation-grants",
        headers=env.headers(human),
        json={
            "client_id": asu["client_id"],
            "scopes": ["staffing", "compensation", "absence", "documents"],
        },
    )
    assert response.status_code == 201, response.text
    return human, response.json()


def exchange(env, asu, grant, **changes):
    form = {
        "grant_type": OBO,
        "client_id": asu["client_id"],
        "client_secret": asu["client_secret"],
        "credential_version_id": asu["credential_version_id"],
        "grant_id": grant["id"],
    }
    form.update(changes)
    return env.client.post("/oauth2/token", data=form)


def assertion(env, asu, key, **changes):
    now = int(env.service.clock.now().timestamp())
    body = {
        "iss": asu["client_id"],
        "sub": asu["username"],
        "aud": "https://acme.mockworkday.local/oauth2/token",
        "iat": now,
        "exp": now + 60,
        "jti": uuid4().hex,
    }
    body.update(changes)
    return jwt.encode(
        body, key, algorithm="RS256", headers={"kid": asu["credential_version_id"]}
    )


def ambient(env, asu, token):
    return env.client.post(
        "/oauth2/token",
        data={
            "grant_type": JWT_BEARER,
            "client_id": asu["client_id"],
            "assertion": token,
        },
    )


def test_T_M3_ID_01_registration_constraints_tenant_and_privileges(env):
    asu, _ = setup_asu(env, ref="helper")
    body = {"slug": "acme", "ref_id": "helper", "display_name": "Duplicate"}
    assert env.admin.post("/admin/agent-registrations", json=body).status_code == 409
    body["slug"] = "globex"
    assert env.admin.post("/admin/agent-registrations", json=body).status_code == 201
    assert (
        env.admin.post(
            asu["path"] + "/modes", json={"slug": "acme", "mode": "DELEGATE"}
        ).status_code
        == 409
    )
    assert (
        env.admin.post(
            asu["path"] + "/modes", json={"slug": "acme", "mode": "AMBIENT"}
        ).status_code
        == 201
    )
    assert (
        env.admin.post(
            asu["path"] + "/modes", json={"slug": "acme", "mode": "THIRD"}
        ).status_code
        == 422
    )
    assert (
        env.admin.patch(
            asu["path"], json={"slug": "globex", "enabled": True}
        ).status_code
        == 404
    )
    tid = seed_id("acme", "tenant", "acme")
    with env.db.tenant_tx(tid) as conn:
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM agent_system_users WHERE tenant_id=:tid",
            )["n"]
            == 2
        )
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM accounts WHERE tenant_id=:tid AND kind='ASU' AND worker_id IS NULL AND NOT ui_sessions_allowed AND password_hash=''",
            )["n"]
            == 2
        )
    with (
        pytest.raises(Exception, match="permission denied"),
        env.db.tenant_tx(tid) as conn,
    ):
        run(conn, "UPDATE agent_registrations SET enabled=true WHERE tenant_id=:tid")
    with env.db.tenant_tx(seed_id("globex", "tenant", "globex")) as conn:
        assert rows(conn, "SELECT id FROM agent_system_users") == []


def test_T_M3_ID_04_05_A_01_grant_renewal_narrowing_attribution(env):
    operations = [op for op in OPERATIONS if op.endswith("_get")]
    asu, _ = setup_asu(env, operations=operations)
    human, g = grant(env, asu)
    response = exchange(env, asu, g)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == env.id("accounts", "bob")
    assert claims["act"]["sub"] == asu["account_id"]
    assert env.get(env.worker("Bob", "/compensation"), token).status_code == 200
    body = {
        "worker_id": env.id("workers", "Bob"),
        "start_date": "2026-11-10",
        "end_date": "2026-11-10",
        "reason": "Synthetic",
    }
    assert (
        env.client.post(
            "/api/v1/business-processes/request-time-off",
            headers={**env.headers(token), "Idempotency-Key": uuid4().hex},
            json=body,
        ).status_code
        == 403
    )
    assert (
        env.client.post(
            "/api/v1/business-processes/request-time-off",
            headers={**env.headers(human), "Idempotency-Key": uuid4().hex},
            json=body,
        ).status_code
        == 201
    )
    env.advance(301)
    renewed = exchange(env, asu, g)
    assert renewed.status_code == 200, renewed.text  # Original human token has expired.
    assert exchange(env, asu, g, scope="compensation").status_code == 200
    assert exchange(env, asu, g, scope="unapproved").status_code == 401
    assert exchange(env, asu, g, credential_version_id="").status_code == 401
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        audit = one(
            conn,
            "SELECT * FROM audit_authz WHERE tenant_id=:tid AND asu_id=:asu AND domain='WORKER_COMPENSATION'",
            asu=UUID(asu["id"]),
        )
        assert audit["by_user_account_id"].hex == asu["account_id"]
        assert audit["on_behalf_of_user_account_id"].hex == env.id("accounts", "bob")
        assert not audit["legacy_delegated"]
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme"), owner=True) as conn:
        run(
            conn,
            "UPDATE accounts SET disabled=true WHERE tenant_id=:tid AND username='bob'",
        )
    assert exchange(env, asu, g).status_code == 401
    assert env.get(env.worker("Bob"), renewed.json()["access_token"]).status_code == 401


def test_T_M3_ID_02_03_ambient_assertion_and_concurrent_replay(env):
    asu, key = setup_asu(env, "AMBIENT")
    encoded = assertion(env, asu, key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: ambient(env, asu, encoded), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 401]
    token = next(r.json()["access_token"] for r in responses if r.status_code == 200)
    assert env.get("/api/v1/workers", token).status_code == 200
    claims = jwt.decode(token, options={"verify_signature": False})
    assert claims["typ"] == "ambient" and claims["sub"] == asu["account_id"]
    body = {
        "worker_id": env.id("workers", "Bob"),
        "start_date": "2026-11-10",
        "end_date": "2026-11-10",
        "reason": "Synthetic",
    }
    assert (
        env.client.post(
            "/api/v1/business-processes/request-time-off",
            headers=env.headers(token),
            json=body,
        ).status_code
        == 403
    )
    now = int(env.service.clock.now().timestamp())
    for changes in (
        {"aud": "https://globex.mockworkday.local/oauth2/token"},
        {"sub": "another-user"},
        {"iss": "another-client"},
        {"exp": now},
        {"exp": now + 61},
        {"iat": now + 6, "exp": now + 60},
        {"jti": ""},
        {"iat": True},
    ):
        assert ambient(env, asu, assertion(env, asu, key, **changes)).status_code == 401
    other, _ = certificate()
    assert ambient(env, asu, assertion(env, asu, other)).status_code == 401
    hmac = jwt.encode(
        {"sub": "x"},
        "synthetic-only-test-secret-32-characters",
        algorithm="HS256",
        headers={"kid": asu["credential_version_id"]},
    )
    assert ambient(env, asu, hmac).status_code == 401
    human = env.login("bob")
    assert (
        env.client.post(
            "/api/v1/delegation-grants",
            headers=env.headers(human),
            json={"client_id": asu["client_id"], "scopes": ["staffing"]},
        ).status_code
        == 422
    )


def test_T_M3_CR_01_02_rotation_revocation_and_secret_outage(env, monkeypatch):
    asu, _ = setup_asu(env)
    _, g = grant(env, asu)
    old_token = exchange(env, asu, g).json()["access_token"]
    route = asu["path"] + "/modes/DELEGATE/credentials"
    rotated = env.admin.post(route, json={"slug": "acme"})
    assert rotated.status_code == 201, rotated.text
    replacement = asu | rotated.json()
    assert env.admin.post(route, json={"slug": "acme"}).status_code == 409
    assert (
        exchange(env, asu, g).status_code == 200
        and exchange(env, replacement, g).status_code == 200
    )
    assert (
        env.admin.post(
            route + "/" + asu["credential_version_id"] + "/revoke",
            json={"slug": "acme"},
        ).status_code
        == 200
    )
    assert env.get(env.worker("Bob"), old_token).status_code == 401
    assert exchange(env, asu, g).status_code == 401
    token = exchange(env, replacement, g).json()["access_token"]
    from mock_workday.credentials import store_setting

    path = Path(store_setting())
    assert path.stat().st_mode & 0o777 == 0o600
    contents = json.loads(path.read_text())
    contents[asu["credential_store_ref"]].pop(replacement["credential_version_id"])
    path.write_text(json.dumps(contents))
    assert env.get(env.worker("Bob"), token).status_code == 503
    assert exchange(env, replacement, g).status_code == 503


def test_T_M3_CR_01_overlap_expiry_and_duplicate_public_key(env):
    asu, _ = setup_asu(env)
    _, g = grant(env, asu)
    replacement = (
        asu
        | env.admin.post(
            asu["path"] + "/modes/DELEGATE/credentials", json={"slug": "acme"}
        ).json()
    )
    env.advance(299)
    assert exchange(env, asu, g).json()["expires_in"] == 1
    env.advance(1)
    assert exchange(env, asu, g).status_code == 401
    assert exchange(env, replacement, g).status_code == 200
    _, pem = certificate()
    first, _ = setup_asu(env, "AMBIENT", pem=pem)
    other = env.admin.post(
        "/admin/agent-registrations",
        json={"slug": "globex", "ref_id": "other", "display_name": "Other"},
    ).json()
    path = "/admin/agent-registrations/" + other["id"]
    assert (
        env.admin.post(
            path + "/modes", json={"slug": "globex", "mode": "AMBIENT"}
        ).status_code
        == 201
    )
    assert (
        env.admin.post(
            path + "/modes/AMBIENT/credentials",
            json={"slug": "globex", "certificate_pem": pem},
        ).status_code
        == 409
    )
    _, weak = certificate(bits=1024)
    assert (
        env.admin.post(
            first["path"] + "/modes/AMBIENT/credentials",
            json={"slug": "acme", "certificate_pem": weak},
        ).status_code
        == 422
    )


def test_T_M3_ID_06_no_legacy_fallback_and_current_operation_ceiling(env):
    asu, _ = setup_asu(env)
    _, g = grant(env, asu)
    assert exchange(env, asu, g, grant_type="client_credentials").status_code == 401
    token = exchange(env, asu, g).json()["access_token"]
    assert env.get(env.worker("Bob"), token).status_code == 200
    assert (
        env.admin.patch(
            asu["path"] + "/modes/DELEGATE",
            json={
                "slug": "acme",
                "enabled": True,
                "scopes": ["staffing"],
                "allowed_operations": [],
            },
        ).status_code
        == 200
    )
    assert env.get(env.worker("Bob"), token).status_code == 403
    assert (
        env.admin.patch(
            asu["path"], json={"slug": "acme", "enabled": False}
        ).status_code
        == 200
    )
    assert env.get(env.worker("Bob"), token).status_code == 401
    assert env.get(env.worker("Bob"), env.delegated("bob")).status_code == 200


def test_T_M3_A_01_identity_audit_failure_rolls_back_issuance(env):
    asu, key = setup_asu(env, "AMBIENT")
    encoded = assertion(env, asu, key)
    tid = seed_id("acme", "tenant", "acme")
    with env.db.tenant_tx(tid, owner=True) as conn:
        run(conn, "REVOKE INSERT ON audit_identity FROM mw_app")
    try:
        assert ambient(env, asu, encoded).status_code == 503
    finally:
        with env.db.tenant_tx(tid, owner=True) as conn:
            run(conn, "GRANT INSERT ON audit_identity TO mw_app")
    assert (
        ambient(env, asu, encoded).status_code == 200
    )  # Failed audit did not consume JTI.


def test_T_M3_A_02_structured_logs_redact_requests(env, caplog):
    caplog.set_level("INFO", logger="mock_workday.requests")
    secret = "must-not-appear-in-logs"
    response = env.client.post(
        "/oauth2/token?private=" + secret,
        data={"grant_type": "password", "username": "bob", "password": secret},
        headers={"X-Request-Id": 'id"escaped'},
    )
    assert response.status_code == 401
    logs = [
        json.loads(r.message)
        for r in caplog.records
        if r.name == "mock_workday.requests"
    ]
    assert logs[-1]["status"] == 401 and logs[-1]["request_id"] == 'id"escaped'
    assert logs[-1]["route"] == "/oauth2/token"
    assert secret not in json.dumps(logs)
    assert response.headers["X-Request-Id"] == 'id"escaped'


def test_T_M3_CR_02_aws_reference_activation_and_binding(env, monkeypatch):
    import boto3
    from botocore.stub import Stubber

    from mock_workday.auth import hash_secret

    asu, _ = setup_asu(env)
    monkeypatch.setenv("MW_CREDENTIAL_STORE", "aws")
    sm = boto3.client(
        "secretsmanager",
        region_name="us-east-2",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    monkeypatch.setattr(env.service.storage, "secret_client", lambda tenant: sm)
    route = asu["path"] + "/modes/DELEGATE/credentials"
    version = uuid4().hex
    value = {
        "tenant_id": str(seed_id("acme", "tenant", "acme")),
        "asu_id": str(UUID(asu["id"])),
        "mode": "DELEGATE",
        "secret_hash": hash_secret("new-synthetic-secret"),
    }
    with Stubber(sm) as stub:
        assert env.admin.post(route, json={"slug": "acme"}).status_code == 422
        stub.add_response(
            "get_secret_value",
            {"VersionId": version, "SecretString": json.dumps(value)},
            {"SecretId": asu["credential_store_ref"], "VersionId": version},
        )
        response = env.admin.post(
            route, json={"slug": "acme", "existing_version": version}
        )
        assert response.status_code == 201, response.text
        assert "client_secret" not in response.json()
        _, g = grant(env, asu)
        replacement = asu | response.json() | {"client_secret": "new-synthetic-secret"}
        stub.add_response(
            "get_secret_value",
            {"VersionId": version, "SecretString": json.dumps(value)},
        )
        token = exchange(env, replacement, g)
        assert token.status_code == 200, token.text
        wrong = value | {"tenant_id": str(seed_id("globex", "tenant", "globex"))}
        stub.add_response(
            "get_secret_value",
            {"VersionId": version, "SecretString": json.dumps(wrong)},
        )
        assert (
            env.get(env.worker("Bob"), token.json()["access_token"]).status_code == 503
        )
        stub.assert_no_pending_responses()


def test_T_M3_A_01_bp_history_and_replay_attribution(env):
    asu, _ = setup_asu(env)
    _, g = grant(env, asu)
    token = exchange(env, asu, g).json()["access_token"]
    body = {
        "worker_id": env.id("workers", "Bob"),
        "start_date": "2026-11-10",
        "end_date": "2026-11-10",
        "reason": "Synthetic day",
    }
    headers = {
        **env.headers(token),
        "Idempotency-Key": uuid4().hex,
        "X-Request-Id": "asu-bp-test",
    }
    path = "/api/v1/business-processes/request-time-off"
    first = env.client.post(path, json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert env.client.post(path, json=body, headers=headers).status_code == 201
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        history = one(
            conn,
            "SELECT * FROM bp_history WHERE tenant_id=:tid AND asu_id=:asu",
            asu=UUID(asu["id"]),
        )
        assert history["by_user_account_id"].hex == asu["account_id"]
        assert history["on_behalf_of_user_account_id"].hex == env.id("accounts", "bob")
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM bp_history WHERE tenant_id=:tid AND asu_id=:asu",
                asu=UUID(asu["id"]),
            )["n"]
            == 1
        )
        obj = one(
            conn,
            "SELECT * FROM audit_objects WHERE tenant_id=:tid AND request_id='asu-bp-test'",
        )
        assert obj["credential_version_id"].hex == asu["credential_version_id"]
    env.admin.post(
        asu["path"]
        + "/modes/DELEGATE/credentials/"
        + asu["credential_version_id"]
        + "/revoke",
        json={"slug": "acme"},
    )
    assert env.client.post(path, json=body, headers=headers).status_code == 401


def test_T_M3_CR_01_failed_credential_write_preserves_old_version(env, monkeypatch):
    from mock_workday.errors import APIError

    asu, _ = setup_asu(env)
    _, g = grant(env, asu)

    def fail(*args):
        raise APIError(503, "SERVICE_UNAVAILABLE")

    monkeypatch.setattr("mock_workday.identity.write_credential", fail)
    assert (
        env.admin.post(
            asu["path"] + "/modes/DELEGATE/credentials", json={"slug": "acme"}
        ).status_code
        == 503
    )
    assert exchange(env, asu, g).status_code == 200
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        version = one(
            conn,
            "SELECT * FROM credential_versions WHERE tenant_id=:tid AND id=:id",
            id=UUID(asu["credential_version_id"]),
        )
        assert version["accept_until"] is None


def test_T_M3_ID_01_disabled_identity_seed_is_explicit_and_deterministic(env):
    from mock_workday.identity import seed_registrations

    seed_registrations(env.db)
    seed_registrations(env.db)
    for slug in ("acme", "globex"):
        with env.db.tenant_tx(seed_id(slug, "tenant", slug)) as conn:
            assert (
                one(
                    conn,
                    "SELECT count(*) AS n FROM agent_system_users WHERE tenant_id=:tid",
                )["n"]
                == 2
            )
            assert (
                one(
                    conn,
                    "SELECT count(*) AS n FROM credential_versions WHERE tenant_id=:tid",
                )["n"]
                == 0
            )
            for row in rows(
                conn, "SELECT * FROM agent_system_users WHERE tenant_id=:tid"
            ):
                assert row["id"] == seed_id(
                    slug, "agent_system_users", "core-lab:" + row["mode"]
                )
                assert not row["enabled"]


@pytest.mark.parametrize("target", ["asu", "client", "account"])
def test_T_M3_CR_02_disable_checks_existing_tokens(env, target):
    asu, _ = setup_asu(env)
    _, g = grant(env, asu)
    token = exchange(env, asu, g).json()["access_token"]
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme"), owner=True) as conn:
        if target == "asu":
            run(
                conn,
                "UPDATE agent_system_users SET enabled=false WHERE tenant_id=:tid AND id=:id",
                id=UUID(asu["id"]),
            )
        elif target == "client":
            run(
                conn,
                "UPDATE api_clients SET disabled=true WHERE tenant_id=:tid AND client_id=:id",
                id=asu["client_id"],
            )
        else:
            run(
                conn,
                "UPDATE accounts SET disabled=true WHERE tenant_id=:tid AND id=:id",
                id=UUID(asu["account_id"]),
            )
    assert env.get(env.worker("Bob"), token).status_code == 401
    assert exchange(env, asu, g).status_code == 401


def test_T_M3_ID_03_certificate_caps_token_lifetime(env):
    key, pem = certificate(expires=30)
    asu, _ = setup_asu(env, "AMBIENT", pem=pem)
    response = ambient(env, asu, assertion(env, asu, key))
    assert response.status_code == 200, response.text
    assert response.json()["expires_in"] == 30
    env.advance(30)
    assert (
        env.get("/api/v1/workers", response.json()["access_token"]).status_code == 401
    )


def test_T_M3_ID_05_current_human_reach_is_not_frozen(env):
    asu, _ = setup_asu(env)
    _, g = grant(env, asu, user="alice")
    token = exchange(env, asu, g).json()["access_token"]
    assert env.get(env.worker("Bob"), token).status_code == 200
    env.revoke_role("MANAGER", "SO-ENG", "P-ENG-DIR")
    assert env.get(env.worker("Bob"), token).status_code == 404


def test_T_M3_A_01_failed_activation_audit_leaves_secret_inert(env, monkeypatch):
    from mock_workday import identity
    from mock_workday.credentials import local_read, store_setting
    from mock_workday.errors import APIError

    asu, _ = setup_asu(env)
    _, g = grant(env, asu)
    original = identity.identity_audit

    def fail(*args, **kwargs):
        raise APIError(503, "AUDIT_UNAVAILABLE")

    monkeypatch.setattr(identity, "identity_audit", fail)
    response = env.admin.post(
        asu["path"] + "/modes/DELEGATE/credentials", json={"slug": "acme"}
    )
    assert response.status_code == 503
    monkeypatch.setattr(identity, "identity_audit", original)
    assert exchange(env, asu, g).status_code == 200
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        versions = rows(conn, "SELECT * FROM credential_versions WHERE tenant_id=:tid")
        assert len(versions) == 1
        assert versions[0]["accept_until"] is None
    material = local_read(store_setting())[asu["credential_store_ref"]]
    assert len(material) == 2
    inert = next(key for key in material if key != asu["credential_version_id"])
    assert exchange(env, asu | {"credential_version_id": inert}, g).status_code == 401


def test_T_M3_CR_02_tenant_secret_clients_share_tagged_session_refresh(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import Mock

    from mock_workday.storage import Storage

    tenants = [{"id": uuid4(), "enabled": True} for _ in range(2)]
    storage = Storage(
        {str(t["id"]): {"bucket": "synthetic", "kms_key_id": "key"} for t in tenants},
        "tenant-role",
    )
    storage.sts = Mock()
    storage.sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "synthetic",
            "SecretAccessKey": "synthetic",
            "SessionToken": "synthetic",
            "Expiration": datetime.now(UTC) + timedelta(seconds=900),
        }
    }
    sessions = []

    def session(**kwargs):
        result = Mock()
        result.client.side_effect = [Mock(), Mock()]
        sessions.append(result)
        return result

    monkeypatch.setattr("mock_workday.storage.boto3.session.Session", session)
    first = storage.secret_client(tenants[0])
    assert storage.secret_client(tenants[0]) is first
    assert storage.secret_client(tenants[1]) is not first
    assert [[c.args[0] for c in s.client.call_args_list] for s in sessions] == [
        ["s3", "secretsmanager"],
        ["s3", "secretsmanager"],
    ]
    assert [c.kwargs["Tags"] for c in storage.sts.assume_role.call_args_list] == [
        [{"Key": "tenant", "Value": str(t["id"])}] for t in tenants
    ]
    cache_key = ("tenant-role", str(tenants[0]["id"]))
    storage.sessions[cache_key] = (datetime.now(UTC), Mock())
    assert storage.secret_client(tenants[0]) is not first
    assert len(sessions) == 3
