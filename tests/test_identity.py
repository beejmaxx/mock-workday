import jwt
import pytest
from sqlalchemy.exc import DBAPIError

from mock_workday.db import rows, run
from mock_workday.ids import seed_id


def test_T_ID_01_cross_tenant(env):
    token = env.login()
    r = env.client.get(
        env.worker("Eve"),
        headers={**env.headers(token), "Host": "globex.mockworkday.local"},
    )
    assert r.status_code == 401


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
def test_T_ID_02_algorithm_allowlist(env, algorithm):
    claims = jwt.decode(env.login(), options={"verify_signature": False})
    token = jwt.encode(
        claims,
        "" if algorithm == "none" else "synthetic-secret" * 3,
        algorithm=algorithm,
        headers={"kid": "fake"},
    )
    assert env.get(env.worker("Bob"), token).status_code == 401


def test_T_ID_03_expiry(env):
    token = env.login()
    env.advance(301)
    assert env.get(env.worker("Bob"), token).status_code == 401


def test_T_ID_04_disabled_account(env):
    token = env.login()
    assert (
        env.admin.post(
            "/admin/accounts/" + env.id("accounts", "alice") + "/disable",
            json={"slug": "acme"},
        ).status_code
        == 200
    )
    assert env.get(env.worker("Bob"), token).status_code == 401


def test_T_ID_05_revoked_grant(env):
    human, grant = env.grant()
    token = env.exchange(grant).json()["access_token"]
    path = "/api/v1/delegation-grants/" + grant["id"]
    assert env.client.delete(path, headers=env.headers(human)).status_code == 204
    assert env.client.delete(path, headers=env.headers(human)).status_code == 204
    assert env.get(env.worker("Bob"), token).status_code == 401


def test_T_ID_06_disabled_client(env):
    token = env.delegated()
    assert (
        env.admin.post(
            "/admin/api-clients/" + env.id("api_clients", "hr-assistant") + "/disable",
            json={"slug": "acme"},
        ).status_code
        == 200
    )
    assert env.get(env.worker("Bob"), token).status_code == 401


def test_T_ID_07_exchange_other_client(env):
    _, grant = env.grant()
    r = env.exchange(grant, "assistant")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_GRANT"


def test_T_ID_08_scope_ceiling(env):
    r = env.client.post(
        "/api/v1/delegation-grants",
        headers=env.headers(env.login()),
        json={"client_id": "assistant", "scopes": ["compensation"]},
    )
    assert r.status_code == 422


def test_T_ID_09_key_rotation(env):
    token = env.login()
    kid = jwt.get_unverified_header(token)["kid"]
    new = env.admin.post("/admin/signing-keys/rotate").json()["kid"]
    assert kid != new
    assert jwt.get_unverified_header(env.login())["kid"] == new
    assert env.get(env.worker("Bob"), token).status_code == 200
    assert {
        k["kid"] for k in env.client.get("/.well-known/jwks.json").json()["keys"]
    } == {kid, new}
    assert env.admin.post("/admin/signing-keys/" + kid + "/retire").status_code == 200
    assert env.get(env.worker("Bob"), token).status_code == 401
    assert {
        k["kid"] for k in env.client.get("/.well-known/jwks.json").json()["keys"]
    } == {new}


def test_T_ID_10_ignored_tenant_fields(env):
    token = env.login()
    r = env.get(
        env.worker("Bob"),
        token,
        params={"tenant_id": env.id("tenant", "globex", "globex"), "slug": "globex"},
    )
    assert r.status_code == 200
    r = env.client.post(
        "/api/v1/delegation-grants",
        headers=env.headers(token),
        json={
            "client_id": "assistant",
            "scopes": ["staffing"],
            "tenant_id": "globex",
            "slug": "globex",
        },
    )
    assert r.status_code == 201
    assert env.exchange(r.json(), "assistant").status_code == 200


def test_T_DB_01_rls_without_context(env):
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        assert len(rows(conn, "SELECT id FROM workers WHERE tenant_id=:tid")) == 9
    with env.db.app.begin() as conn:
        # Test RLS alone, deliberately without the application's WHERE clause.
        run(conn, "RESET app.tenant_id")
        try:
            assert rows(conn, "SELECT * FROM workers") == []
        except DBAPIError:
            pass


@pytest.mark.parametrize("table", ["audit_authz", "audit_objects"])
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
def test_T_DB_02_append_only(env, table, verb):
    with (
        pytest.raises(DBAPIError),
        env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn,
    ):
        sql = (
            f"UPDATE {table} SET request_id=request_id WHERE tenant_id=:tid"
            if verb == "UPDATE"
            else f"DELETE FROM {table} WHERE tenant_id=:tid"
        )
        run(conn, sql)


@pytest.mark.parametrize("which", ["client", "grant"])
def test_T_ID_11_current_scopes(env, which):
    _, grant = env.grant()
    token = env.exchange(grant).json()["access_token"]
    assert env.get(env.worker("Bob", "/compensation"), token).status_code == 200
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme"), owner=True) as conn:
        if which == "client":
            run(
                conn,
                "UPDATE api_clients SET scope_ceiling=ARRAY['staffing'] WHERE tenant_id=:tid AND client_id='hr-assistant'",
            )
        else:
            run(
                conn,
                "UPDATE delegation_grants SET scopes=ARRAY['staffing'] WHERE tenant_id=:tid AND id=:id",
                id=grant["id"],
            )
    assert env.get(env.worker("Bob", "/compensation"), token).status_code == 403


def test_T_ID_11_current_isu_ceiling(env):
    token = env.isu()
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme"), owner=True) as conn:
        run(
            conn,
            "UPDATE api_clients SET scope_ceiling=ARRAY[]::text[] WHERE tenant_id=:tid AND client_id='directory-sync'",
        )
    assert env.get(env.worker("Bob"), token).status_code == 404


@pytest.mark.parametrize("kind", ["isu", "delegated"])
def test_T_ID_12_grants_direct_human_only(env, kind):
    token = env.isu() if kind == "isu" else env.delegated()
    assert env.get("/api/v1/delegation-grants", token).status_code == 403
    assert (
        env.client.post(
            "/api/v1/delegation-grants",
            headers=env.headers(token),
            json={"client_id": "assistant", "scopes": []},
        ).status_code
        == 403
    )
    assert (
        env.client.delete(
            "/api/v1/delegation-grants/" + env.id("accounts", "alice"),
            headers=env.headers(token),
        ).status_code
        == 403
    )


def test_T_ID_12_grant_ownership_ttl(env):
    alice, grant = env.grant("alice", scopes=["staffing"], ttl=60)
    bob = env.login("bob")
    assert env.get("/api/v1/delegation-grants", bob).json() == []
    assert env.get("/api/v1/delegation-grants", alice).json()[0]["id"] == grant["id"]
    assert (
        env.client.delete(
            "/api/v1/delegation-grants/" + grant["id"], headers=env.headers(bob)
        ).status_code
        == 404
    )
    delegated = env.exchange(grant).json()["access_token"]
    env.advance(60)
    assert env.get(env.worker("Bob"), delegated).status_code == 401
    assert env.exchange(grant).status_code == 401
    for ttl in (59, 86401):
        assert (
            env.client.post(
                "/api/v1/delegation-grants",
                headers=env.headers(alice),
                json={"client_id": "assistant", "scopes": [], "ttl_seconds": ttl},
            ).status_code
            == 422
        )


@pytest.mark.parametrize(
    "form",
    [
        {"grant_type": "password", "username": "alice", "password": "wrong"},
        {
            "grant_type": "password",
            "username": "isu-directory",
            "password": "pw-isu-directory",
        },
        {
            "grant_type": "client_credentials",
            "client_id": "assistant",
            "client_secret": "secret-assistant",
        },
        {
            "grant_type": "client_credentials",
            "client_id": "directory-sync",
            "client_secret": "wrong",
        },
    ],
)
def test_T_ID_13_invalid_login(env, form):
    assert env.client.post("/oauth2/token", data=form).status_code == 401


def test_T_ID_14_host_and_error_contract(env):
    r = env.client.get(
        "/api/v1/workers",
        headers={"Host": "unknown.mockworkday.local", "X-Request-Id": "test-request"},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "TENANT_NOT_FOUND"
    assert (
        r.headers["X-Request-Id"] == r.json()["error"]["request_id"] == "test-request"
    )
    r = env.client.get("/api/v1/workers")
    assert r.status_code == 401
    assert r.headers["X-Request-Id"] == r.json()["error"]["request_id"]
