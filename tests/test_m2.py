import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest

from mock_workday.db import one, run


def post(env, path, token, body, key=None):
    return env.client.post(
        path,
        headers={**env.headers(token), "Idempotency-Key": key or uuid4().hex},
        json=body,
    )


def change(env, worker="Bob", compensation=True, token=None, key=None):
    body = {
        "worker_id": env.id("workers", worker),
        "position_id": env.id("positions", "P-FIN-1"),
        "effective_date": "2026-11-01",
    }
    if compensation:
        body["compensation"] = {"annual_salary": 140000, "currency": "USD"}
    return post(
        env,
        "/api/v1/business-processes/change-job",
        token or env.login("alice"),
        body,
        key,
    )


def created(response):
    assert response.status_code == 201, response.text
    return response.json()["event"]


def action(env, event, user, verb="approve", key=None, token=None):
    step = next(s for s in event["steps"] if s["step_order"] == event["current_step"])
    return post(
        env,
        event["href"] + "/" + verb,
        token or env.login(user),
        {
            "expected_step": step["step_key"],
            "expected_version": event["version"],
            "comment": "reviewed",
        },
        key,
    )


def approved(response):
    assert response.status_code == 200, response.text
    return response.json()["event"]


def time_off(env):
    return created(
        post(
            env,
            "/api/v1/business-processes/request-time-off",
            env.login("bob"),
            {
                "worker_id": env.id("workers", "Bob"),
                "start_date": "2026-11-10",
                "end_date": "2026-11-12",
                "reason": "Holiday",
            },
        )
    )


def complete(env):
    event = created(change(env))
    event = approved(action(env, event, "priya"))
    return approved(action(env, event, "connie"))


def november(env, day=1):
    assert (
        env.admin.post(
            "/admin/clock", json={"now": f"2026-11-{day:02d}T09:00:00Z"}
        ).status_code
        == 200
    )


def race(*calls):
    barrier = Barrier(len(calls))

    def go(call):
        barrier.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(go, calls))


def fault(env, event, kind, value=None, count=1, client=None, method="POST"):
    match = {"method": method, "path_prefix": event["href"]}
    if client:
        match["client_id"] = client
    response = env.admin.post(
        "/admin/faults",
        json={
            "slug": "acme",
            "match": match,
            "type": kind,
            "value": value,
            "count": count,
        },
    )
    assert response.status_code == 201, response.text


def test_T_B_01_initial_steps(env):
    event = created(change(env))
    assert event["status"] == "IN_PROGRESS"
    assert [s["step_key"] for s in event["steps"]] == [
        "RECEIVING_MANAGER",
        "COMPENSATION_PARTNER",
    ]
    assert event["steps"][0]["initial_assignee_account_ids"] == [
        env.id("accounts", "priya")
    ]
    assert (
        env.id("accounts", "connie")
        in event["steps"][1]["initial_assignee_account_ids"]
    )


def test_T_B_02_receiving_manager_disclosure(env):
    event = created(change(env))
    token = env.login("priya")
    response = env.get(event["href"], token)
    assert response.status_code == 200, response.text
    assert "compensation" not in response.json()["payload"]
    assert "comment" not in response.json()["steps"][1]
    assert env.get(env.worker("Bob"), token).status_code == 404


def test_T_B_03_complete_revisions(env):
    event = complete(env)
    assert event["status"] == "SUCCESSFULLY_COMPLETED"
    with env.db.tenant_tx(UUID(env.id("tenant", "acme"))) as conn:
        for table in ("job_revisions", "compensation_revisions"):
            assert (
                one(
                    conn,
                    f"SELECT count(*) AS n FROM {table} WHERE tenant_id=:tid AND bp_event_id=:eid",
                    eid=UUID(event["id"]),
                )["n"]
                == 1
            )
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM bp_history WHERE tenant_id=:tid AND event_id=:eid",
                eid=UUID(event["id"]),
            )["n"]
            == 3
        )


def test_T_B_04_effective_transfer(env):
    complete(env)
    assert env.get(env.worker("Bob"), env.login("alice")).status_code == 200
    assert env.get(env.worker("Bob"), env.login("priya")).status_code == 404
    november(env)
    assert env.get(env.worker("Bob"), env.login("alice")).status_code == 404
    assert env.get(env.worker("Bob"), env.login("priya")).status_code == 200


def test_T_B_05_historical_current_authority(env):
    complete(env)
    november(env)
    assert (
        env.get(env.worker("Bob") + "?as_of=2026-10-15", env.login("alice")).status_code
        == 404
    )


def test_T_B_06_pending_subject(env):
    created(change(env))
    response = change(env, token=env.login("carol"))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "PENDING_CHANGE_EXISTS"


def test_T_B_07_concurrent_subject(env):
    token = env.login()
    responses = race(lambda: change(env, token=token), lambda: change(env, token=token))
    assert sorted(r.status_code for r in responses) == [201, 409]


def test_T_B_08_approve_deny_race(env):
    event = created(change(env))
    token = env.login("priya")
    responses = race(
        lambda: action(env, event, "priya", token=token),
        lambda: action(env, event, "priya", "deny", token=token),
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(r for r in responses if r.status_code == 200).json()
    loser = next(r for r in responses if r.status_code == 409).json()
    assert loser["error"]["code"] == (
        "VERSION_CONFLICT" if winner["status"] == "IN_PROGRESS" else "INVALID_STATE"
    )


def test_T_B_09_effective_date_passed(env):
    event = approved(action(env, created(change(env)), "priya"))
    november(env, 2)
    response = action(env, event, "connie")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "EFFECTIVE_DATE_PASSED"
    with env.db.tenant_tx(UUID(env.id("tenant", "acme"))) as conn:
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM job_revisions WHERE tenant_id=:tid AND bp_event_id=:eid",
                eid=UUID(event["id"]),
            )["n"]
            == 0
        )
    assert approved(action(env, event, "alice", "cancel"))["status"] == "CANCELED"


def test_T_B_10_initiator_cannot_approve(env):
    assert action(env, created(change(env)), "alice").status_code == 403


@pytest.mark.parametrize("verb", ["approve", "deny"])
def test_T_B_11_subject_cannot_act(env, verb):
    assert action(env, created(change(env)), "bob", verb).status_code == 403


def test_T_B_12_skipped_compensation(env):
    event = created(change(env, compensation=False))
    assert event["steps"][1]["status"] == "SKIPPED"
    assert approved(action(env, event, "priya"))["status"] == "SUCCESSFULLY_COMPLETED"


def test_T_B_13_occupied_position(env):
    response = post(
        env,
        "/api/v1/business-processes/change-job",
        env.login(),
        {
            "worker_id": env.id("workers", "Bob"),
            "position_id": env.id("positions", "P-PLAT-1"),
            "effective_date": "2026-11-01",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "POSITION_OCCUPIED"


def test_T_B_14_delegated_scope(env):
    token = env.delegated("alice", "assistant", ["staffing"])
    assert change(env, token=token).status_code == 403


def test_T_B_15_destination_race(env):
    token = env.login()
    responses = race(
        lambda: change(env, "Bob", token=token),
        lambda: change(env, "Frank", token=token),
    )
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert (
        next(r for r in responses if r.status_code == 409).json()["error"]["code"]
        == "POSITION_OCCUPIED"
    )


def test_T_T_01_time_off_completion(env):
    assert (
        approved(action(env, time_off(env), "alice"))["status"]
        == "SUCCESSFULLY_COMPLETED"
    )


def test_T_T_02_dynamic_manager(env):
    event = time_off(env)
    complete(env)
    november(env)
    assert action(env, event, "alice").status_code == 403
    assert approved(action(env, event, "priya"))["status"] == "SUCCESSFULLY_COMPLETED"


def test_T_T_03_cancel_approve_race(env):
    event = time_off(env)
    bob = env.login("bob")
    alice = env.login()
    responses = race(
        lambda: action(env, event, "bob", "cancel", token=bob),
        lambda: action(env, event, "alice", token=alice),
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert (
        next(r for r in responses if r.status_code == 409).json()["error"]["code"]
        == "INVALID_STATE"
    )


def test_T_T_04_time_off_visibility(env):
    assert env.get(time_off(env)["href"], env.login("frank")).status_code == 404


def test_T_T_05_seed_injection_text(env):
    path = "/api/v1/business-process-events/" + env.id("bp_events", "Grace Time Off")
    response = env.get(path, env.login("frank"))
    assert response.status_code == 200, response.text
    assert "ignore" in response.json()["payload"]["reason"].lower()


def test_T_I_01_replay(env):
    token = env.login()
    first = change(env, token=token, key="same")
    second = change(env, token=token, key="same")
    assert first.status_code == second.status_code == 201
    assert second.headers["Idempotent-Replay"] == "true"
    assert first.json() == second.json()


def test_T_I_02_reused_key(env):
    token = env.login()
    created(change(env, token=token, key="same"))
    response = change(env, compensation=False, token=token, key="same")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_T_I_03_timeout_after(env):
    event = created(change(env))
    fault(env, event, "timeout_after_commit")
    assert action(env, event, "priya", key="approve").status_code == 504
    response = action(env, event, "priya", key="approve")
    assert response.status_code == 200, response.text
    assert response.headers["Idempotent-Replay"] == "true"
    assert response.json()["event"]["version"] == 2
    with env.db.tenant_tx(UUID(env.id("tenant", "acme"))) as conn:
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM bp_history WHERE tenant_id=:tid AND event_id=:eid AND action='APPROVE'",
                eid=UUID(event["id"]),
            )["n"]
            == 1
        )


def test_T_I_04_timeout_before(env):
    event = created(change(env))
    fault(env, event, "timeout_before_commit")
    assert action(env, event, "priya", key="approve").status_code == 504
    assert env.get(event["href"], env.login()).json()["version"] == 1
    response = action(env, event, "priya", key="approve")
    assert response.status_code == 200, response.text
    assert "Idempotent-Replay" not in response.headers


def test_T_I_05_replay_redaction(env):
    event = approved(action(env, created(change(env)), "priya"))
    fault(env, event, "timeout_after_commit")
    assert action(env, event, "connie", key="approve").status_code == 504
    env.revoke_role("COMPENSATION_PARTNER", "SO-ENG", "P-COMP-1")
    response = action(env, event, "connie", key="approve")
    assert response.status_code == 200, response.text
    assert response.headers["Idempotent-Replay"] == "true"
    assert "compensation" not in response.json()["event"]["payload"]
    assert "comment" not in response.json()["event"]["steps"][1]


def test_T_I_06_revoked_grant_replay(env):
    event = created(change(env))
    human, grant = env.grant("priya")
    token = env.exchange(grant).json()["access_token"]
    fault(env, event, "timeout_after_commit")
    assert action(env, event, "priya", token=token, key="approve").status_code == 504
    response = env.client.delete(
        "/api/v1/delegation-grants/" + grant["id"], headers=env.headers(human)
    )
    assert response.status_code == 204, response.text
    assert action(env, event, "priya", token=token, key="approve").status_code == 401


def test_T_I_07_expired_key(env):
    event = created(change(env, compensation=False))
    approved(action(env, event, "priya", key="approve"))
    env.advance(86400)
    response = action(env, event, "priya", key="approve")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "INVALID_STATE"


def test_T_I_08_concurrent_duplicate(env):
    token = env.login()
    responses = race(
        lambda: change(env, token=token, key="same"),
        lambda: change(env, token=token, key="same"),
    )
    assert [r.status_code for r in responses] == [201, 201]
    assert sum(r.headers.get("Idempotent-Replay") == "true" for r in responses) == 1
    assert responses[0].json() == responses[1].json()


def test_T_F_01_matching_counts(env, monkeypatch):
    sleeps = []
    monkeypatch.setattr("mock_workday.faults.sleep", sleeps.append)
    event = created(change(env))
    token = env.login("priya")
    fault(env, event, "latency", value=75, method="GET")
    assert env.get("/api/v1/workers", token).status_code == 200
    assert sleeps == []
    assert env.get(event["href"], token).status_code == 200
    assert env.get(event["href"], token).status_code == 200
    assert sleeps == [0.075]
    for status in (429, 503):
        fault(
            env,
            event,
            "status",
            value=status,
            count=2,
            method="GET",
            client="hr-assistant",
        )
        assert env.get(event["href"], token).status_code == 200
        delegated = env.delegated("priya")
        assert [env.get(event["href"], delegated).status_code for _ in range(3)] == [
            status,
            status,
            200,
        ]


def test_T_B_16_current_compensation_assignee(env):
    event = created(change(env))
    env.revoke_role("COMPENSATION_PARTNER", "SO-ENG", "P-COMP-1")
    token = env.login("connie")
    assert "compensation" not in env.get(event["href"], token).json()["payload"]
    event = approved(action(env, event, "priya"))
    assert (
        env.get(event["href"], token).json()["payload"]["compensation"]["annual_salary"]
        == 140000
    )
    approved(action(env, event, "connie"))
    result = env.get(event["href"], token).json()
    assert "compensation" not in result["payload"]
    assert "comment" not in result["steps"][1]


def test_T_B_17_list_filters_and_pagination(env):
    event = created(change(env))
    token = env.login("priya")
    path = "/api/v1/business-process-events"
    response = env.get(
        path
        + "?awaiting_me=true&status=IN_PROGRESS&subject="
        + env.id("workers", "Bob"),
        token,
    )
    assert response.status_code == 200, response.text
    assert [e["id"] for e in response.json()["data"]] == [event["id"]]
    assert "compensation" not in response.json()["data"][0]["payload"]
    approved(action(env, event, "priya"))
    assert env.get(path + "?awaiting_me=true", token).json()["data"] == []
    with env.db.tenant_tx(UUID(env.id("tenant", "acme"))) as conn:
        before = one(
            conn,
            "SELECT count(*) AS n FROM audit_authz WHERE tenant_id=:tid AND decision='DENY'",
        )["n"]
    assert env.get(path, env.login("bob")).status_code == 200
    with env.db.tenant_tx(UUID(env.id("tenant", "acme"))) as conn:
        assert (
            one(
                conn,
                "SELECT count(*) AS n FROM audit_authz WHERE tenant_id=:tid AND decision='DENY'",
            )["n"]
            == before
        )
    time_off(env)
    first = env.get(path + "?limit=1", env.login("bob")).json()
    assert first["next_cursor"]
    second = env.get(
        path, env.login("bob"), params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert first["data"][0]["id"] != second["data"][0]["id"]
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"effective_date": "2026-10-01"},
        {"compensation": {"annual_salary": 0, "currency": "USD"}},
        {"compensation": {"annual_salary": 100, "currency": "ZZZ"}},
    ],
)
def test_T_B_18_change_validation(env, mutation):
    body = {
        "worker_id": env.id("workers", "Bob"),
        "position_id": env.id("positions", "P-FIN-1"),
        "effective_date": "2026-11-01",
        **mutation,
    }
    assert (
        post(
            env, "/api/v1/business-processes/change-job", env.login(), body
        ).status_code
        == 422
    )


def test_T_B_19_final_vacancy_recheck(env):
    event = approved(action(env, created(change(env)), "priya"))
    with env.db.tenant_tx(UUID(env.id("tenant", "acme")), owner=True) as conn:
        run(
            conn,
            """INSERT INTO job_revisions (id,tenant_id,worker_id,position_id,effective_date,recorded_at)
            VALUES (:id,:tid,:worker,:position,'2026-11-01',:now)""",
            id=uuid4(),
            worker=UUID(env.id("workers", "Grace")),
            position=UUID(env.id("positions", "P-FIN-1")),
            now=env.service.clock.now(),
        )
    response = action(env, event, "connie")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "POSITION_OCCUPIED"
    assert env.get(event["href"], env.login("connie")).json()["version"] == 2


def test_T_T_06_validation_and_self(env):
    body = {
        "worker_id": env.id("workers", "Bob"),
        "start_date": "2026-01-02",
        "end_date": "2026-01-01",
        "reason": "past",
    }
    path = "/api/v1/business-processes/request-time-off"
    assert post(env, path, env.login("bob"), body).status_code == 422
    body["end_date"] = "2026-01-03"
    assert post(env, path, env.login("alice"), body).status_code == 403
    assert post(env, path, env.login("bob"), body).status_code == 201


def test_T_I_09_key_and_canonical_body(env):
    token = env.login()
    path = "/api/v1/business-processes/request-time-off"
    body = {
        "worker_id": env.id("workers", "Bob"),
        "start_date": "2026-11-10",
        "end_date": "2026-11-12",
        "reason": "Holiday",
    }
    assert (
        env.client.post(path, headers=env.headers(token), json=body).status_code == 400
    )
    assert post(env, path, token, body, "a" * 129).status_code == 400
    token = env.login("bob")
    first = post(env, path, token, body, "canonical")
    assert first.status_code == 201, first.text
    response = env.client.post(
        path,
        headers={
            **env.headers(token),
            "Idempotency-Key": "canonical",
            "Content-Type": "application/json",
        },
        content=json.dumps(dict(reversed(list(body.items()))), indent=2),
    )
    assert response.status_code == 201, response.text
    assert response.headers["Idempotent-Replay"] == "true"


def test_T_I_10_disabled_client_replay(env):
    event = created(change(env))
    token = env.delegated("priya")
    approved(action(env, event, "priya", token=token, key="approve"))
    assert (
        env.admin.post(
            "/admin/api-clients/" + env.id("api_clients", "hr-assistant") + "/disable",
            json={"slug": "acme"},
        ).status_code
        == 200
    )
    assert action(env, event, "priya", token=token, key="approve").status_code == 401


def test_T_F_02_audit_failure(env):
    event = created(change(env))
    fault(env, event, "audit_write_failure")
    assert action(env, event, "priya", key="approve").status_code == 503
    assert env.get(event["href"], env.login()).json()["version"] == 1
    response = action(env, event, "priya", key="approve")
    assert response.status_code == 200, response.text
    assert "Idempotent-Replay" not in response.headers
    fault(env, event, "audit_write_failure", method="GET")
    assert env.get(event["href"], env.login("connie")).status_code == 503
    assert env.get(event["href"], env.login("connie")).status_code == 200
    fault(env, event, "audit_write_failure", method="GET")
    assert env.get(event["href"], env.login("frank")).status_code == 404


def test_T_F_03_final_audit_rollback(env):
    event = approved(action(env, created(change(env)), "priya"))
    with env.db.owner.begin() as conn:
        run(conn, "REVOKE INSERT ON audit_objects FROM mw_app")
    try:
        assert action(env, event, "connie", key="final").status_code == 503
    finally:
        with env.db.owner.begin() as conn:
            run(conn, "GRANT INSERT ON audit_objects TO mw_app")
    with env.db.tenant_tx(UUID(env.id("tenant", "acme"))) as conn:
        for table in ("job_revisions", "compensation_revisions"):
            assert (
                one(
                    conn,
                    f"SELECT count(*) AS n FROM {table} WHERE tenant_id=:tid AND bp_event_id=:id",
                    id=UUID(event["id"]),
                )["n"]
                == 0
            )
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
                "SELECT count(*) AS n FROM idempotency_records WHERE tenant_id=:tid AND idem_key='final'",
            )["n"]
            == 0
        )
    assert (
        approved(action(env, event, "connie", key="final"))["status"]
        == "SUCCESSFULLY_COMPLETED"
    )


def test_T_I_11_minimal_receipt_after_scope_loss(env):
    event = created(change(env))
    _, grant = env.grant("priya")
    token = env.exchange(grant).json()["access_token"]
    first = action(env, event, "priya", token=token, key="approve")
    assert first.status_code == 200, first.text
    with env.db.tenant_tx(UUID(env.id("tenant", "acme")), owner=True) as conn:
        run(
            conn,
            "UPDATE delegation_grants SET scopes=ARRAY[]::text[] WHERE tenant_id=:tid AND id=:id",
            id=UUID(grant["id"]),
        )
    replay = action(env, event, "priya", token=token, key="approve")
    assert replay.status_code == 200, replay.text
    assert replay.headers["Idempotent-Replay"] == "true"
    assert replay.json() == {
        k: first.json()[k] for k in ("operation_id", "status", "resource")
    }


def test_T_B_20_cross_tenant_event(env):
    event = created(change(env))
    token = env.login("dave", "globex")
    headers = {**env.headers(token), "Host": "globex.mockworkday.local"}
    assert env.client.get(event["href"], headers=headers).status_code == 404
    response = env.client.post(
        event["href"] + "/approve",
        headers={**headers, "Idempotency-Key": "cross"},
        json={"expected_step": "RECEIVING_MANAGER", "expected_version": 1},
    )
    assert response.status_code == 404
    assert env.get(event["href"], env.login()).json()["version"] == 1


@pytest.mark.parametrize("stalled", [False, True])
def test_T_B_21_manager_fallback(env, stalled):
    if stalled:
        env.revoke_role("MANAGER", "SO-EXEC", "P-CEO")
    body = {
        "worker_id": env.id("workers", "Bob"),
        "position_id": env.id("positions", "P-ENG-2"),
        "effective_date": "2026-11-01",
    }
    event = created(
        post(env, "/api/v1/business-processes/change-job", env.login(), body)
    )
    assert event["steps"][0]["current_assignee_account_ids"] == (
        [] if stalled else [env.id("accounts", "dana")]
    )
    if stalled:
        assert action(env, event, "dana").status_code == 404
        assert approved(action(env, event, "alice", "cancel"))["status"] == "CANCELED"
    else:
        assert (
            approved(action(env, event, "dana"))["status"] == "SUCCESSFULLY_COMPLETED"
        )
