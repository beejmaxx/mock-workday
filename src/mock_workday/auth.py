import hashlib
import hmac
import secrets
from dataclasses import dataclass
from uuid import UUID, uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .db import one, rows, run
from .errors import APIError


def hash_secret(value):
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return salt + ":" + digest.hex()


def check_secret(value, stored):
    salt, expected = stored.split(":")
    actual = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return hmac.compare_digest(actual.hex(), expected)


def rotate_key(conn, now):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    kid = uuid4()
    run(conn, "UPDATE signing_keys SET state='VERIFY_ONLY' WHERE state='ACTIVE'")
    run(
        conn,
        "INSERT INTO signing_keys VALUES (:id, :private, :public, 'ACTIVE', :now)",
        id=kid,
        private=private,
        public=public,
        now=now,
    )
    return kid.hex


def jwks(conn):
    keys = []
    for row in rows(
        conn,
        "SELECT * FROM signing_keys WHERE state <> 'RETIRED' ORDER BY created_at, id",
    ):
        public = serialization.load_pem_public_key(row["public_pem"].encode())
        keys.append(
            {
                **jwt.algorithms.RSAAlgorithm.to_jwk(public, as_dict=True),
                "kid": row["id"].hex,
                "use": "sig",
                "alg": "RS256",
            }
        )
    return {"keys": keys}


@dataclass(frozen=True)
class Principal:
    tenant_id: UUID
    account_id: UUID
    kind: str
    worker_id: UUID | None
    client_id: str | None
    grant_id: UUID | None
    scopes: frozenset[str] | None
    agent_id: UUID | None = None
    asu_id: UUID | None = None
    credential_version_id: UUID | None = None
    actor_account_id: UUID | None = None
    operations: frozenset[str] | None = None
    expires_at: int | None = None


def active_grant(conn, gid, now):
    try:
        gid = UUID(str(gid))
    except (ValueError, TypeError):
        return None
    return one(
        conn,
        """SELECT * FROM delegation_grants
        WHERE tenant_id=:tid AND id=:id AND revoked_at IS NULL AND expires_at>:now""",
        id=gid,
        now=now,
    )


def issue_token(
    conn, tenant, form, now, storage=None, request_id="token", identity_context=None
):
    from .identity import exchange, identity_audit

    kind = form.grant_type
    client = grant = None
    extra = {}
    if kind == "password":
        account = one(
            conn,
            "SELECT * FROM accounts WHERE tenant_id=:tid AND username=:name",
            name=form.username,
        )
        if (
            not account
            or account["kind"] != "HUMAN"
            or account["disabled"]
            or not check_secret(form.password or "", account["password_hash"])
        ):
            raise APIError(401, "INVALID_GRANT")
        typ, scopes = "human", None
    else:
        client = one(
            conn,
            "SELECT * FROM api_clients WHERE tenant_id=:tid AND client_id=:id",
            id=form.client_id,
        )
        if not client or client["disabled"]:
            raise APIError(401, "INVALID_CLIENT")
        if client["asu_id"]:
            account, typ, scopes, grant, extra = exchange(
                conn, tenant, client, form, now, storage
            )
        else:
            if (
                not client
                or client["disabled"]
                or not check_secret(form.client_secret or "", client["secret_hash"])
            ):
                raise APIError(401, "INVALID_CLIENT")
            if kind == "client_credentials":
                account = one(
                    conn,
                    "SELECT * FROM accounts WHERE tenant_id=:tid AND id=:id AND kind='ISU'",
                    id=client["isu_account_id"],
                )
                typ, scopes = "isu", client["scope_ceiling"]
            elif kind == "urn:ietf:params:oauth:grant-type:token-exchange":
                grant = active_grant(conn, form.grant_id, now)
                if not grant or grant["client_id"] != client["client_id"]:
                    raise APIError(401, "INVALID_GRANT")
                account = one(
                    conn,
                    "SELECT * FROM accounts WHERE tenant_id=:tid AND id=:id AND kind='HUMAN'",
                    id=grant["user_account_id"],
                )
                typ, scopes = (
                    "delegated",
                    sorted(set(grant["scopes"]) & set(client["scope_ceiling"])),
                )
            else:
                raise APIError(401, "INVALID_GRANT")
            if not account or account["disabled"]:
                raise APIError(401, "INVALID_GRANT")
    issuer = f"https://{tenant['slug']}.mockworkday.local"
    claims = {
        "iss": issuer,
        "aud": issuer + "/api",
        "sub": account["id"].hex,
        "typ": typ,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + 300,
        "jti": uuid4().hex,
    }
    if client:
        claims.update(client_id=client["client_id"], scope=" ".join(scopes))
    if grant:
        claims.update(gid=grant["id"].hex, act={"client_id": client["client_id"]})
    claims.update(extra)
    if claims["exp"] <= int(now.timestamp()):
        raise APIError(401, "INVALID_GRANT")
    key = one(conn, "SELECT * FROM signing_keys WHERE state='ACTIVE'")
    if not key:
        raise APIError(401, "INVALID_GRANT")
    token = jwt.encode(
        claims, key["private_pem"], algorithm="RS256", headers={"kid": key["id"].hex}
    )
    identity_audit(
        conn,
        "token_issued",
        now,
        request_id,
        by=UUID(claims["act"]["sub"]) if extra and grant else account["id"],
        behalf=account["id"] if extra and grant else None,
        agent=UUID(extra["agent_id"]) if extra else None,
        asu=UUID(extra["asu_id"]) if extra else None,
        version=UUID(extra["credential_version_id"]) if extra else None,
    )
    if identity_context is not None:
        identity_context.update(
            account_id=account["id"].hex,
            client_id=client["client_id"] if client else None,
            asu_id=extra.get("asu_id"),
            agent_id=extra.get("agent_id"),
            credential_version_id=extra.get("credential_version_id"),
            by_user_account_id=claims.get("act", {}).get("sub", account["id"].hex),
        )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": claims["exp"] - int(now.timestamp()),
    }


def authenticate(conn, tenant, authorization, now, storage=None):
    from .credentials import read_credential
    from .identity import active_identity

    try:
        scheme, token = authorization.split(" ", 1)
        if scheme.lower() != "bearer":
            raise ValueError()
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise ValueError()
        key = one(
            conn,
            "SELECT * FROM signing_keys WHERE id=:id AND state<>'RETIRED'",
            id=UUID(header["kid"]),
        )
        if not key:
            raise ValueError()
        issuer = f"https://{tenant['slug']}.mockworkday.local"
        c = jwt.decode(
            token,
            key["public_pem"],
            algorithms=["RS256"],
            issuer=issuer,
            audience=issuer + "/api",
            options={
                "verify_exp": False,
                "verify_iat": False,
                "require": ["iss", "aud", "sub", "typ", "iat", "exp", "jti"],
            },
        )
        if c["iss"] != issuer or c["aud"] != issuer + "/api":
            raise ValueError()
        if not c["exp"] > now.timestamp() >= c["iat"] - 5:
            raise ValueError()
        account = one(
            conn,
            "SELECT * FROM accounts WHERE tenant_id=:tid AND id=:id AND NOT disabled",
            id=UUID(c["sub"]),
        )
        if not account:
            raise ValueError()
        typ, cid, gid = c["typ"], c.get("client_id"), None
        scopes = None
        client = None
        extra = {}
        if typ == "human":
            if (
                account["kind"] != "HUMAN"
                or cid is not None
                or "gid" in c
                or "act" in c
                or "asu_id" in c
            ):
                raise ValueError()
        elif typ in ("isu", "delegated", "ambient"):
            client = one(
                conn,
                "SELECT * FROM api_clients WHERE tenant_id=:tid AND client_id=:id AND NOT disabled",
                id=cid,
            )
            if not client or not isinstance(c.get("scope"), str):
                raise ValueError()
            scopes = frozenset(c["scope"].split()) & frozenset(client["scope_ceiling"])
            if typ == "isu":
                if (
                    account["kind"] != "ISU"
                    or client["isu_account_id"] != account["id"]
                ):
                    raise ValueError()
            elif typ == "ambient":
                if account["kind"] != "ASU" or not client["asu_id"]:
                    raise ValueError()
            else:
                grant = active_grant(conn, c.get("gid"), now)
                if (
                    account["kind"] != "HUMAN"
                    or not grant
                    or grant["client_id"] != cid
                    or grant["user_account_id"] != account["id"]
                    or (not client["asu_id"] and c.get("act") != {"client_id": cid})
                ):
                    raise ValueError()
                gid = grant["id"]
                scopes &= frozenset(grant["scopes"])
        else:
            raise ValueError()
        if client and client["asu_id"]:
            asu, version = active_identity(
                conn, client, c.get("credential_version_id"), now
            )
            read_credential(storage, tenant, asu, version)
            if (
                c.get("asu_id") != asu["id"].hex
                or c.get("agent_id") != asu["registration_id"].hex
            ):
                raise ValueError()
            if asu["mode"] == "DELEGATE":
                if typ != "delegated" or c.get("act") != {
                    "client_id": cid,
                    "sub": asu["account_id"].hex,
                }:
                    raise ValueError()
            elif (
                typ != "ambient"
                or account["id"] != asu["account_id"]
                or "gid" in c
                or "act" in c
            ):
                raise ValueError()
            if not isinstance(c.get("operations"), list) or any(
                not isinstance(op, str) for op in c["operations"]
            ):
                raise ValueError()
            extra = {
                "agent_id": asu["registration_id"],
                "asu_id": asu["id"],
                "credential_version_id": version["id"],
                "actor_account_id": asu["account_id"],
                "operations": frozenset(c["operations"])
                & frozenset(client["allowed_operations"]),
            }
        elif "asu_id" in c or "agent_id" in c or "credential_version_id" in c:
            raise ValueError()
        return Principal(
            tenant["id"],
            account["id"],
            typ,
            account["worker_id"],
            cid,
            gid,
            scopes,
            expires_at=c["exp"],
            **extra,
        )
    except APIError as exc:
        if exc.status == 401:
            raise APIError(401, "UNAUTHENTICATED") from None
        raise
    except (ValueError, TypeError, KeyError, AttributeError, jwt.PyJWTError):
        raise APIError(401, "UNAUTHENTICATED") from None
