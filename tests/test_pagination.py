import pytest


def scan(env, token, params=None, cursor=None):
    seen = []
    while True:
        r = env.get(
            "/api/v1/workers",
            token,
            params={**(params or {}), **({"cursor": cursor} if cursor else {})},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        seen.extend(w["id"] for w in data["data"])
        cursor = data["next_cursor"]
        if cursor is None:
            return seen


def test_T_P_01_keyset_scan(env):
    ids = scan(env, env.isu(), {"limit": 3})
    assert len(ids) == len(set(ids)) == 9
    assert ids == sorted(ids)


@pytest.mark.parametrize(
    "change", ["account", "filters", "tamper", "path", "client", "expired"]
)
def test_T_P_02_cursor_binding(env, change):
    token = env.isu()
    r = env.get("/api/v1/workers", token, params={"limit": 1})
    cursor = r.json()["next_cursor"]
    path = "/api/v1/workers"
    params = {"cursor": cursor, "limit": 1}
    if change == "account":
        token = env.login("dana")
    elif change == "filters":
        params["employee_id"] = "E1001"
    elif change == "tamper":
        params["cursor"] = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    elif change == "path":
        path = "/api/v1/organizations/" + env.id("organizations", "SO-ENG") + "/workers"
    elif change == "client":
        token = env.isu("eng-sync")
    else:
        env.advance(901)
        token = env.isu()
    r = env.get(path, token, params=params)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "INVALID_CURSOR"


def test_T_P_03_revocation_mid_scan(env):
    token = env.login()
    params = {
        "org": env.id("organizations", "SO-ENG"),
        "include_subordinates": True,
        "limit": 1,
    }
    r = env.get("/api/v1/workers", token, params=params)
    assert len(r.json()["data"]) == 1
    env.revoke_role("MANAGER", "SO-ENG", "P-ENG-DIR")
    r = env.get(
        "/api/v1/workers", token, params={**params, "cursor": r.json()["next_cursor"]}
    )
    assert r.status_code == 200 and r.json() == {"data": [], "next_cursor": None}


def configure(env):
    r = env.admin.post(
        "/admin/rate-limits",
        json={
            "slug": "acme",
            "client_id": "directory-sync",
            "capacity": 2,
            "refill_per_second": 1,
        },
    )
    assert r.status_code == 200


def test_T_P_04_rate_limit(env):
    token = env.isu()
    configure(env)
    for _ in range(2):
        assert env.get(env.worker("Bob"), token).status_code == 200
    r = env.get(env.worker("Bob"), token)
    assert r.status_code == 429 and r.headers["Retry-After"] == "1"
    env.advance(1)
    assert env.get(env.worker("Bob"), token).status_code == 200


def test_T_P_05_resume_after_throttle(env):
    token = env.isu()
    configure(env)
    seen = []
    cursor = None
    throttled = False
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        r = env.get("/api/v1/workers", token, params=params)
        if r.status_code == 429:
            throttled = True
            env.advance(int(r.headers["Retry-After"]))
            continue
        assert r.status_code == 200, r.text
        seen.extend(row["id"] for row in r.json()["data"])
        cursor = r.json()["next_cursor"]
        if cursor is None:
            break
    assert throttled
    assert len(seen) == len(set(seen)) == 9


def test_T_P_06_org_filter(env):
    r = env.get(
        "/api/v1/workers",
        env.login(),
        params={"org": env.id("organizations", "SO-ENG"), "include_subordinates": True},
    )
    assert {w["descriptor"] for w in r.json()["data"]} == {"Bob", "Frank", "Grace"}


def test_T_P_07_document_pagination_filters(env):
    token = env.login()
    seen = []
    cursor = None
    while True:
        r = env.get(
            "/api/v1/documents",
            token,
            params={"limit": 1, **({"cursor": cursor} if cursor else {})},
        )
        assert r.status_code == 200
        seen.extend(d["id"] for d in r.json()["data"])
        cursor = r.json()["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 4
    r = env.get(
        "/api/v1/documents", token, params={"owner_worker_id": env.id("workers", "Bob")}
    )
    assert [x["title"] for x in r.json()["data"]] == ["Bob Performance Review"]
    assert env.get("/api/v1/workers", token, params={"limit": 201}).status_code == 422
