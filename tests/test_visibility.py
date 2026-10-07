import pytest

from mock_workday.db import run
from mock_workday.ids import seed_id


@pytest.mark.parametrize(
    "user,target,basic,comp",
    [
        pytest.param("bob", "Bob", 200, 200, id="T-V-01"),
        pytest.param("bob", "Alice", 404, None, id="T-V-02"),
        pytest.param("alice", "Bob", 200, 403, id="T-V-03"),
        pytest.param("alice", "Grace", 200, 403, id="T-V-04"),
        pytest.param("frank", "Bob", 404, None, id="T-V-05"),
        pytest.param("carol", "Bob", 200, 200, id="T-V-06"),
        pytest.param("carol", "Grace", 404, None, id="T-V-07"),
        pytest.param("henry", "Grace", 200, 200, id="T-V-08"),
        pytest.param("connie", "Bob", 200, 200, id="T-V-09"),
        pytest.param("dana", "Grace", 200, 403, id="T-V-11"),
    ],
)
def test_visibility(env, user, target, basic, comp):
    token = env.login(user)
    assert env.get(env.worker(target), token).status_code == basic
    if comp:
        assert env.get(env.worker(target, "/compensation"), token).status_code == comp


def test_T_V_10_other_tenant_object(env):
    r = env.get("/api/v1/workers/" + env.id("workers", "Eve", "globex"), env.login())
    assert r.status_code == 404


def test_T_V_12_delegated_manager(env):
    token = env.delegated("alice", "assistant", ["staffing"])
    assert env.get(env.worker("Bob"), token).status_code == 200
    assert env.get(env.worker("Bob", "/compensation"), token).status_code == 403


@pytest.mark.parametrize(
    "client,scopes,expected",
    [
        pytest.param("assistant", ["staffing"], 403, id="T-V-13"),
        pytest.param("hr-assistant", ["staffing", "compensation"], 200, id="T-V-14"),
        pytest.param("hr-assistant", ["staffing"], 403, id="T-V-15"),
    ],
)
def test_delegated_scopes(env, client, scopes, expected):
    token = env.delegated("carol", client, scopes)
    assert env.get(env.worker("Bob", "/compensation"), token).status_code == expected


def test_T_V_16_history_filter(env):
    r = env.get(env.worker("Bob", "/history"), env.login())
    assert r.status_code == 200
    assert [row["type"] for row in r.json()] == ["JOB"]
    assert "annualSalary" not in r.text
    r = env.get(env.worker("Bob", "/history"), env.login("carol"))
    assert {row["type"] for row in r.json()} == {"JOB", "COMPENSATION"}


def test_T_V_17_role_revocation(env):
    token = env.login("carol")
    assert env.get(env.worker("Bob"), token).status_code == 200
    env.revoke_role("HR_PARTNER", "SO-ENG", "P-HRBP-1")
    assert env.get(env.worker("Bob"), token).status_code == 404


def test_T_V_18_constrained_isu(env):
    token = env.isu("eng-sync")
    for name, status in [("Bob", 200), ("Grace", 200), ("Priya", 404)]:
        assert env.get(env.worker(name), token).status_code == status


def test_T_V_19_isu_compensation(env):
    assert env.get(env.worker("Bob", "/compensation"), env.isu()).status_code == 403


def test_T_V_20_vacant_role_does_not_prune(env):
    env.revoke_role("HR_PARTNER", "SO-PLAT", "P-HRBP-2")
    r = env.admin.post(
        "/admin/role-assignments",
        json={
            "slug": "acme",
            "role": "HR_PARTNER",
            "org_id": env.id("organizations", "SO-PLAT"),
            "position_id": env.id("positions", "P-ENG-2"),
        },
    )
    assert r.status_code == 201
    assert env.get(env.worker("Grace"), env.login("carol")).status_code == 200


def test_T_V_21_current_only_reach(env):
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme"), owner=True) as conn:
        run(
            conn,
            "UPDATE security_groups SET access_rights='CURRENT_ONLY' WHERE tenant_id=:tid AND role='MANAGER'",
        )
    token = env.login()
    assert env.get(env.worker("Bob"), token).status_code == 200
    assert env.get(env.worker("Grace"), token).status_code == 404


def test_T_V_22_organizations_and_direct_reports(env):
    token = env.login()
    oid = env.id("organizations", "SO-ENG")
    r = env.get(env.worker("Bob", "/organizations"), token)
    assert r.status_code == 200 and r.json()["id"] == oid
    r = env.get("/api/v1/organizations/" + oid, env.login("bob"))
    assert r.status_code == 200 and r.json()["refId"] == "SO-ENG"
    r = env.get("/api/v1/organizations/" + oid + "/workers", token)
    assert {w["descriptor"] for w in r.json()["data"]} == {"Bob", "Frank"}
    r = env.get(env.worker("Alice", "/direct-reports"), token)
    assert {w["descriptor"] for w in r.json()["data"]} == {"Bob", "Frank"}
    assert (
        env.get(env.worker("Bob", "/organizations"), env.isu("eng-sync")).status_code
        == 403
    )


@pytest.mark.parametrize("principal", ["human", "delegated", "isu"])
def test_T_V_23_position_reference(env, principal):
    worker = env.get(env.worker("Bob"), env.login("bob")).json()
    position_ref = worker["primaryPosition"]
    if principal == "human":
        token = env.login("bob")
    elif principal == "delegated":
        token = env.delegated("bob", "assistant", [])
    else:
        token = env.isu("eng-sync")
    result = env.get(position_ref["href"], token)
    assert result.status_code == 200
    assert result.json() == {
        **position_ref,
        "refId": "P-ENG-1",
        "organization": worker["primarySupervisoryOrganization"],
    }
    # Position references use tenant authentication, not worker visibility or scopes.
    assert (
        env.get("/api/v1/positions/" + env.id("positions", "P-CEO"), token).status_code
        == 200
    )
    assert (
        env.get(
            "/api/v1/positions/" + env.id("positions", "P-GX-1", "globex"), token
        ).status_code
        == 404
    )
    assert (
        env.get("/api/v1/positions/00000000000000000000000000000000", token).status_code
        == 404
    )
    assert env.client.get(position_ref["href"]).status_code == 401
