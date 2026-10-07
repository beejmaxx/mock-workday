import pytest

from mock_workday.db import one, run
from mock_workday.ids import seed_id


def document(env, title):
    return "/api/v1/documents/" + env.id("documents", title)


def body(env, **changes):
    return {
        "title": "Synthetic note",
        "content": "Synthetic content",
        "domain": "DOC_ORG",
        "classification": "CONFIDENTIAL",
        "org_id": env.id("organizations", "SO-ENG"),
        **changes,
    }


def test_T_D_01_self_documents(env):
    token = env.login("bob")
    for title, status in [
        ("Employee Handbook", 200),
        ("Engineering Reorg Plan", 404),
        ("Bob Performance Review", 200),
    ]:
        assert env.get(document(env, title), token).status_code == status


def test_T_D_02_manager_documents(env):
    token = env.login()
    for title in ("Engineering Reorg Plan", "Bob Performance Review"):
        assert env.get(document(env, title), token).status_code == 200


def test_T_D_03_sibling_document(env):
    assert (
        env.get(document(env, "Bob Performance Review"), env.login("frank")).status_code
        == 404
    )


def test_T_D_04_classification_minimum(env):
    assert (
        env.client.post(
            "/api/v1/documents",
            headers=env.headers(env.login("carol")),
            json=body(env, classification="INTERNAL"),
        ).status_code
        == 422
    )


def test_T_D_05_self_write_denied(env):
    data = body(
        env, domain="DOC_WORKER", org_id=None, owner_worker_id=env.id("workers", "Bob")
    )
    assert (
        env.client.post(
            "/api/v1/documents", headers=env.headers(env.login("bob")), json=data
        ).status_code
        == 403
    )


def test_T_D_06_composition_gap(env):
    token = env.login("carol")
    r = env.get(env.worker("Bob", "/compensation"), token)
    assert r.status_code == 200
    salary = str(r.json()["annualSalary"])
    r = env.client.post(
        "/api/v1/documents", headers=env.headers(token), json=body(env, content=salary)
    )
    assert r.status_code == 201
    assert "content" not in r.json()
    r = env.get(r.json()["href"], env.login())
    assert r.status_code == 200 and r.json()["content"] == salary


def test_T_D_07_audit_failure(env):
    token = env.login()
    with env.db.owner.begin() as conn:
        run(conn, "REVOKE INSERT ON audit_authz FROM mw_app")
    try:
        r = env.get(document(env, "Engineering Reorg Plan"), token)
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "AUDIT_UNAVAILABLE"
        assert "Synthetic engineering" not in r.text
        # Denials still fail with the normal status when their best-effort audit fails.
        assert env.get(env.worker("Priya"), token).status_code == 404
    finally:
        with env.db.owner.begin() as conn:
            run(conn, "GRANT INSERT ON audit_authz TO mw_app")


def test_T_D_08_metadata_only_list(env):
    token = env.login()
    r = env.get("/api/v1/documents", token)
    assert r.status_code == 200
    assert len(r.json()["data"]) == 4
    for doc in r.json()["data"]:
        assert "content" not in doc
        assert set(doc) == {
            "id",
            "descriptor",
            "href",
            "title",
            "domain",
            "classification",
            "owner",
            "org",
            "created_at",
        }
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        assert (
            one(conn, "SELECT count(*) AS n FROM audit_authz WHERE tenant_id=:tid")["n"]
            == 0
        )
    assert len(env.get("/api/v1/documents", env.login("bob")).json()["data"]) == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"domain": "DOC_WORKER"},
        {"owner_worker_id": "00000000000000000000000000000001"},
        {"domain": "DOC_TENANT", "org_id": None, "classification": "PUBLIC"},
        {"content": "é" * 32769},
    ],
)
def test_T_D_09_creation_constraints(env, changes):
    assert (
        env.client.post(
            "/api/v1/documents",
            headers=env.headers(env.login("carol")),
            json=body(env, **changes),
        ).status_code
        == 422
    )


def test_T_D_09_tenant_document_seed_only(env):
    data = body(env, domain="DOC_TENANT", org_id=None, classification="INTERNAL")
    assert (
        env.client.post(
            "/api/v1/documents", headers=env.headers(env.login("carol")), json=data
        ).status_code
        == 403
    )


def test_T_D_10_write_audit_atomicity(env):
    token = env.login("carol")
    tid = seed_id("acme", "tenant", "acme")
    with env.db.tenant_tx(tid) as conn:
        before = one(conn, "SELECT count(*) AS n FROM documents WHERE tenant_id=:tid")[
            "n"
        ]
    with env.db.owner.begin() as conn:
        run(conn, "REVOKE INSERT ON audit_objects FROM mw_app")
    try:
        r = env.client.post(
            "/api/v1/documents", headers=env.headers(token), json=body(env)
        )
        assert r.status_code == 503
        with env.db.tenant_tx(tid) as conn:
            assert (
                one(conn, "SELECT count(*) AS n FROM documents WHERE tenant_id=:tid")[
                    "n"
                ]
                == before
            )
            assert (
                one(
                    conn,
                    "SELECT count(*) AS n FROM audit_authz WHERE tenant_id=:tid AND action='WRITE'",
                )["n"]
                == 0
            )
    finally:
        with env.db.owner.begin() as conn:
            run(conn, "GRANT INSERT ON audit_objects TO mw_app")
