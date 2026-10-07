from fastapi.testclient import TestClient

from mock_workday.app import create_apps
from mock_workday.db import one, rows
from mock_workday.ids import seed_id


def test_T_AD_01_admin_separate_disabled(env):
    assert env.client.post("/admin/reset").status_code == 404
    assert not any(
        path.startswith("/admin")
        for path in env.client.get("/openapi.json").json()["paths"]
    )
    public, private = create_apps(env.db, test_admin=False)
    assert private is None
    with TestClient(public) as client:
        assert client.post("/admin/reset").status_code == 404


def test_T_AD_02_reset_deterministic(env):
    token = env.isu()
    before = env.get("/api/v1/workers", token).json()["data"]
    before_key = env.client.get("/.well-known/jwks.json").json()["keys"][0]["kid"]
    env.revoke_role("MANAGER", "SO-ENG", "P-ENG-DIR")
    env.advance(99)
    assert env.admin.post("/admin/reset").status_code == 200
    after = env.get("/api/v1/workers", env.isu()).json()["data"]
    assert before == after
    assert env.service.clock.now().isoformat() == "2026-10-07T09:00:00+00:00"
    assert (
        env.client.get("/.well-known/jwks.json").json()["keys"][0]["kid"] != before_key
    )
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        assert (
            one(conn, "SELECT policy_version FROM tenant_config WHERE tenant_id=:tid")[
                "policy_version"
            ]
            == 1
        )
        assert rows(conn, "SELECT * FROM audit_objects WHERE tenant_id=:tid") == []


def test_T_AD_03_controlled_clock(env):
    # Calls and database work cannot consume the simulated time budget.
    before = env.service.clock.now()
    for _ in range(3):
        env.login()
    assert env.service.clock.now() == before
    env.advance(1.5)
    assert (env.service.clock.now() - before).total_seconds() == 1.5
