import jwt
import pytest


@pytest.mark.parametrize("host", ["acme.mockworkday.internal", "acme.lab.example.com"])
def test_T_M3_N_01_DNS_01_transport_alias_preserves_token_identity(
    env, monkeypatch, host
):
    monkeypatch.setenv("MW_PUBLIC_DOMAIN", "lab.example.com")
    response = env.client.post(
        "/oauth2/token",
        headers={"Host": host},
        data={"grant_type": "password", "username": "alice", "password": "pw-alice"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    canonical = jwt.decode(env.login(), options={"verify_signature": False})
    claims = jwt.decode(token, options={"verify_signature": False})
    assert (claims["iss"], claims["aud"]) == (canonical["iss"], canonical["aud"])
    for target in (host, "acme.mockworkday.local"):
        assert (
            env.client.get(
                env.worker("Bob"), headers={"Host": target, **env.headers(token)}
            ).status_code
            == 200
        )
    assert (
        env.client.get(
            env.worker("Bob"),
            headers={"Host": "unknown.mockworkday.internal", **env.headers(token)},
        ).status_code
        == 404
    )
    assert (
        env.client.get(
            env.worker("Bob"),
            headers={"Host": "globex.mockworkday.internal", **env.headers(token)},
        ).status_code
        == 401
    )
