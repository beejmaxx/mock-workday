import json
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID, uuid5

from common import deployment

NAMESPACE = UUID("b2dbf256-9eed-4f28-874b-afbd58c9e3d2")


def main():
    config = deployment()
    base = config["url"]
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, expected, *, token=None, data=None, method=None, form=False):
        headers = {
            "Host": "acme." + config["public_domain"]
            if config.get("public_domain")
            else "acme.mockworkday.local"
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        if data is not None:
            headers["Content-Type"] = (
                "application/x-www-form-urlencoded" if form else "application/json"
            )
            data = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
        request = urllib.request.Request(
            base + path, headers=headers, data=data, method=method
        )
        try:
            response = http.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            if response.status != expected:
                raise SystemExit(
                    f"{request.method} {path}: expected {expected}, got {response.status}"
                )
            return json.loads(body) if body else None

    human = call(
        "/oauth2/token",
        200,
        form=True,
        data={"grant_type": "password", "username": "alice", "password": "pw-alice"},
    )["access_token"]
    bob = uuid5(NAMESPACE, "acme:workers:Bob").hex
    eve = uuid5(NAMESPACE, "globex:workers:Eve").hex
    call("/api/v1/workers/" + bob, 200, token=human)
    call("/api/v1/workers/" + bob + "/compensation", 403, token=human)
    call("/api/v1/workers/" + eve, 404, token=human)
    grant = call(
        "/api/v1/delegation-grants",
        201,
        token=human,
        data={"client_id": "assistant", "scopes": ["staffing"], "ttl_seconds": 300},
    )
    try:
        delegated = call(
            "/oauth2/token",
            200,
            form=True,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": "assistant",
                "client_secret": "secret-assistant",
                "grant_id": grant["id"],
            },
        )["access_token"]
        call("/api/v1/workers/" + bob, 200, token=delegated)
    finally:
        call(
            "/api/v1/delegation-grants/" + grant["id"],
            204,
            token=human,
            method="DELETE",
        )
    call("/api/v1/workers/" + bob, 401, token=delegated)
    print(
        "PASS: Alice login; Bob read 200; compensation 403; cross-tenant 404; revoked grant 401"
    )


if __name__ == "__main__":
    main()
