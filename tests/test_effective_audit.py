from datetime import date
from uuid import uuid4

from mock_workday.db import one, rows, run
from mock_workday.ids import seed_id


def test_T_E_01_before_first_revision(env):
    r = env.get(
        env.worker("Bob", "/compensation"),
        env.login("bob"),
        params={"as_of": "2024-06-01"},
    )
    assert r.status_code == 404


def future_revisions(env):
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme"), owner=True) as conn:
        for table, fields, values in [
            (
                "job_revisions",
                "position_id",
                {"position_id": seed_id("acme", "positions", "P-FIN-1")},
            ),
            (
                "compensation_revisions",
                "annual_salary,currency",
                {"annual_salary": 140000, "currency": "USD"},
            ),
        ]:
            run(
                conn,
                f"""INSERT INTO {table} (id,tenant_id,worker_id,effective_date,recorded_at,{fields})
                VALUES (:id,:tid,:wid,:day,:now,{",".join(":" + k for k in values)})""",
                id=uuid4(),
                wid=seed_id("acme", "workers", "Bob"),
                day=date(2026, 11, 1),
                now=env.service.clock.now(),
                **values,
            )


def test_T_E_02_future_revisions(env):
    future_revisions(env)
    token = env.login("bob")
    assert (
        env.get(env.worker("Bob", "/compensation"), token).json()["annualSalary"]
        == 120000
    )
    assert (
        env.get(env.worker("Bob"), token).json()["primarySupervisoryOrganization"][
            "descriptor"
        ]
        == "Engineering"
    )
    assert len(env.get(env.worker("Bob", "/history"), token).json()) == 2
    assert (
        env.admin.post("/admin/clock", json={"now": "2026-11-01T00:00:00Z"}).status_code
        == 200
    )
    token = env.login("bob")
    assert (
        env.get(env.worker("Bob", "/compensation"), token).json()["annualSalary"]
        == 140000
    )
    assert (
        env.get(env.worker("Bob"), token).json()["primarySupervisoryOrganization"][
            "descriptor"
        ]
        == "Finance"
    )
    assert env.get(env.worker("Bob"), env.login()).status_code == 404
    assert env.get(env.worker("Bob"), env.login("priya")).status_code == 200
    assert (
        env.get(
            env.worker("Bob"), env.login(), params={"as_of": "2026-10-15"}
        ).status_code
        == 404
    )
    assert (
        env.get(
            env.worker("Bob", "/compensation"), token, params={"as_of": "2026-10-15"}
        ).json()["annualSalary"]
        == 120000
    )


def test_T_E_03_same_date_recorded_order(env):
    future_revisions(env)
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme"), owner=True) as conn:
        run(
            conn,
            """INSERT INTO compensation_revisions
            (id,tenant_id,worker_id,annual_salary,currency,effective_date,recorded_at)
            VALUES (:id,:tid,:wid,150000,'USD','2026-11-01',:now)""",
            id=uuid4(),
            wid=seed_id("acme", "workers", "Bob"),
            now=env.service.clock.now(),
        )
    env.admin.post("/admin/clock", json={"now": "2026-11-01T00:00:00Z"})
    assert (
        env.get(env.worker("Bob", "/compensation"), env.login("bob")).json()[
            "annualSalary"
        ]
        == 150000
    )


def test_T_A_01_sensitive_read(env):
    token = env.login("carol")
    r = env.client.get(
        env.worker("Bob", "/compensation"),
        headers={**env.headers(token), "X-Request-Id": "salary-read"},
    )
    assert r.status_code == 200
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        row = one(
            conn,
            "SELECT * FROM audit_authz WHERE tenant_id=:tid AND request_id='salary-read'",
        )
        assert row["matched_group_id"] == seed_id(
            "acme", "security_groups", "HR Partner"
        )
        assert row["constraining_org_id"] == seed_id("acme", "organizations", "SO-ENG")
        assert row["job_revision_id"] == seed_id("acme", "job_revisions", "Bob")
        assert row["policy_version"] == 1
        assert row["decision"] == "ALLOW"


def test_T_A_02_denial(env):
    assert env.get(env.worker("Alice"), env.login("bob")).status_code == 404
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        row = one(
            conn, "SELECT * FROM audit_authz WHERE tenant_id=:tid AND decision='DENY'"
        )
        assert row["account_id"] == seed_id("acme", "accounts", "bob")
        assert row["reason"] == "NO_GRANT"


def test_T_A_03_grant_audit(env):
    human, grant = env.grant()
    assert (
        env.client.delete(
            "/api/v1/delegation-grants/" + grant["id"], headers=env.headers(human)
        ).status_code
        == 204
    )
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        audit = rows(
            conn,
            "SELECT * FROM audit_objects WHERE tenant_id=:tid AND object_id=:id",
            id=grant["id"],
        )
        assert {row["field"] for row in audit} == {"created", "revoked_at"}
        assert {row["account_id"] for row in audit} == {
            seed_id("acme", "accounts", "carol")
        }


def test_T_A_04_delegated_audit(env):
    token = env.delegated()
    assert env.get(env.worker("Bob", "/compensation"), token).status_code == 200
    with env.db.tenant_tx(seed_id("acme", "tenant", "acme")) as conn:
        row = one(
            conn,
            "SELECT * FROM audit_authz WHERE tenant_id=:tid AND domain='WORKER_COMPENSATION'",
        )
        assert row["client_id"] == "hr-assistant"
        assert row["grant_id"] is not None
        assert row["account_id"] == seed_id("acme", "accounts", "carol")
