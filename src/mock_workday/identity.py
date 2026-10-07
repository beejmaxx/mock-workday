import hashlib
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .auth import active_grant, check_secret, hash_secret
from .credentials import read_credential, write_credential
from .db import one, rows, run
from .errors import APIError

OPERATIONS = frozenset(
    (
        "list_workers_api_v1_workers_get",
        "get_worker_api_v1_workers__wid__get",
        "worker_organizations_api_v1_workers__wid__organizations_get",
        "compensation_api_v1_workers__wid__compensation_get",
        "history_api_v1_workers__wid__history_get",
        "direct_reports_api_v1_workers__wid__direct_reports_get",
        "organization_api_v1_organizations__wid__get",
        "organization_workers_api_v1_organizations__wid__workers_get",
        "position_api_v1_positions__wid__get",
        "time_off_balances_api_v1_workers__wid__time_off_balances_get",
        "list_documents_api_v1_documents_get",
        "create_document_api_v1_documents_post",
        "get_document_api_v1_documents__wid__get",
        "change_job_api_v1_business_processes_change_job_post",
        "time_off_api_v1_business_processes_request_time_off_post",
        "get_event_api_v1_business_process_events__wid__get",
        "list_events_api_v1_business_process_events_get",
        "approve_api_v1_business_process_events__wid__approve_post",
        "deny_api_v1_business_process_events__wid__deny_post",
        "cancel_api_v1_business_process_events__wid__cancel_post",
    )
)
SCOPES = frozenset(("staffing", "compensation", "absence", "documents"))
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
OBO = "urn:ietf:params:oauth:grant-type:token-exchange"


def identity_audit(
    conn,
    action,
    now,
    request_id,
    *,
    by=None,
    behalf=None,
    agent=None,
    asu=None,
    version=None,
    operator=None,
):
    from sqlalchemy.exc import SQLAlchemyError

    from .audit import check_failure

    check_failure(conn)
    try:
        run(
            conn,
            """INSERT INTO audit_identity VALUES
            (:id,:tid,:rid,:action,:operator,:by,:behalf,:agent,:asu,:version,:now)""",
            id=uuid4(),
            rid=request_id,
            action=action,
            operator=operator,
            by=by,
            behalf=behalf,
            agent=agent,
            asu=asu,
            version=version,
            now=now,
        )
    except SQLAlchemyError as exc:
        raise APIError(503, "AUDIT_UNAVAILABLE") from exc


def certificate(pem, now):
    try:
        if "PRIVATE KEY" in pem:
            raise ValueError()
        cert = x509.load_pem_x509_certificate(pem.encode())
        key = cert.public_key()
        if (
            not isinstance(key, rsa.RSAPublicKey)
            or key.key_size < 2048
            or not cert.not_valid_before_utc <= now < cert.not_valid_after_utc
        ):
            raise ValueError()
        fingerprint = hashlib.sha256(
            key.public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        ).hexdigest()
        return key, fingerprint, cert.not_valid_after_utc
    except (ValueError, TypeError, AttributeError) as exc:
        raise APIError(422, "VALIDATION_ERROR") from exc


def registration(conn, rid, *, lock=False):
    row = one(
        conn,
        "SELECT * FROM agent_registrations WHERE tenant_id=:tid AND id=:id"
        + (" FOR UPDATE" if lock else ""),
        id=rid,
    )
    if not row:
        raise APIError(404, "NOT_FOUND")
    return row


def asu_row(conn, rid, mode, *, lock=False):
    registration(conn, rid)
    asu = one(
        conn,
        "SELECT * FROM agent_system_users WHERE tenant_id=:tid AND registration_id=:id AND mode=:mode"
        + (" FOR UPDATE" if lock else ""),
        id=rid,
        mode=mode,
    )
    if not asu:
        raise APIError(404, "NOT_FOUND")
    return asu


def create_registration(
    conn, ref_id, name, now, request_id, *, rid=None, operator="test-admin"
):
    rid = rid or uuid4()
    run(
        conn,
        "INSERT INTO agent_registrations VALUES (:id,:tid,:ref,:name,false,:now)",
        id=rid,
        ref=ref_id,
        name=name,
        now=now,
    )
    identity_audit(
        conn, "registration_created", now, request_id, agent=rid, operator=operator
    )
    return {"id": rid.hex, "enabled": False}


def configure_mode(
    conn, rid, body, now, request_id, *, seed_slug=None, operator="test-admin"
):
    registration(conn, rid, lock=True)
    if (
        not set(body.scopes) <= SCOPES
        or not set(body.allowed_operations) <= OPERATIONS
        or (body.mode == "DELEGATE" and body.group_ids)
    ):
        raise APIError(422, "VALIDATION_ERROR")
    for gid in body.group_ids:
        if not one(
            conn,
            "SELECT id FROM security_groups WHERE tenant_id=:tid AND id=:id AND kind IN ('INTEGRATION_CONSTRAINED','INTEGRATION_UNCONSTRAINED')",
            id=gid,
        ):
            raise APIError(422, "VALIDATION_ERROR")
    aid, asuid, cid = uuid4(), uuid4(), "asu-" + uuid4().hex
    client_wid = uuid4()
    if seed_slug:
        from .ids import seed_id

        asuid = seed_id(seed_slug, "agent_system_users", "core-lab:" + body.mode)
        aid = seed_id(seed_slug, "accounts", "core-lab:" + body.mode)
        client_wid = seed_id(seed_slug, "api_clients", "core-lab:" + body.mode)
        cid = "asu-" + asuid.hex
    reference = body.credential_store_ref or f"{conn.info['tenant_id']}/{asuid}"
    username = "asu_" + asuid.hex
    run(
        conn,
        """INSERT INTO accounts (id,tenant_id,username,kind,password_hash,ui_sessions_allowed)
        VALUES (:id,:tid,:name,'ASU','',false)""",
        id=aid,
        name=username,
    )
    run(
        conn,
        """INSERT INTO api_clients (id,tenant_id,client_id,secret_hash,name,scope_ceiling,allowed_operations)
        VALUES (:id,:tid,:cid,'',:name,:scopes,:operations)""",
        id=client_wid,
        cid=cid,
        name=username,
        scopes=sorted(set(body.scopes)),
        operations=sorted(set(body.allowed_operations)),
    )
    run(
        conn,
        "INSERT INTO agent_system_users VALUES (:id,:tid,:rid,:aid,:cid,:mode,false,:reference)",
        id=asuid,
        rid=rid,
        aid=aid,
        cid=cid,
        mode=body.mode,
        reference=reference,
    )
    run(
        conn,
        "UPDATE api_clients SET asu_id=:asu WHERE tenant_id=:tid AND client_id=:cid",
        asu=asuid,
        cid=cid,
    )
    for gid in body.group_ids:
        run(
            conn,
            "INSERT INTO integration_group_members VALUES (:tid,:gid,:aid)",
            gid=gid,
            aid=aid,
        )
    identity_audit(
        conn,
        "asu_created",
        now,
        request_id,
        agent=rid,
        asu=asuid,
        operator=operator,
    )
    return {
        "id": asuid.hex,
        "account_id": aid.hex,
        "client_id": cid,
        "username": username,
        "mode": body.mode,
        "enabled": False,
        "credential_store_ref": reference,
    }


def enroll(
    conn,
    tenant,
    storage,
    rid,
    mode,
    pem,
    now,
    request_id,
    *,
    existing_version=None,
    operator="test-admin",
):
    asu = asu_row(conn, rid, mode, lock=True)
    accepted = rows(
        conn,
        """SELECT * FROM credential_versions WHERE tenant_id=:tid AND asu_id=:asu
        AND revoked_at IS NULL AND (accept_until IS NULL OR accept_until>:now)
        AND (certificate_expires_at IS NULL OR certificate_expires_at>:now) ORDER BY active_from,id""",
        asu=asu["id"],
        now=now,
    )
    if len(accepted) >= 2:
        raise APIError(409, "CONFLICT")
    version = uuid4() if existing_version is None else UUID(str(existing_version))
    payload = {"tenant_id": str(tenant["id"]), "asu_id": str(asu["id"]), "mode": mode}
    secret = None
    if existing_version:
        payload = read_credential(storage, tenant, asu, {"store_version": version.hex})
    elif mode == "AMBIENT":
        payload["certificate"] = pem
    else:
        secret = secrets.token_urlsafe(32)
        payload["secret_hash"] = hash_secret(secret)
    if mode == "AMBIENT":
        _, fingerprint, expires = certificate(payload.get("certificate"), now)
    else:
        fingerprint = expires = None
        verifier = payload.get("secret_hash", "")
        if (
            len(verifier) != 161
            or verifier[32:33] != ":"
            or any(c not in "0123456789abcdef" for c in verifier.replace(":", ""))
        ):
            raise APIError(422, "VALIDATION_ERROR")
    if not existing_version:
        write_credential(asu["credential_store_ref"], version.hex, payload)
    # Unique fingerprint is global, including retired keys; a rejected secret version stays inert.
    run(
        conn,
        """INSERT INTO credential_versions VALUES
        (:id,:tid,:asu,:store_version,:fingerprint,:now,NULL,NULL,:expires)""",
        id=version,
        asu=asu["id"],
        store_version=version.hex,
        fingerprint=fingerprint,
        now=now,
        expires=expires,
    )
    for old in accepted:
        until = (
            min(old["accept_until"], now + timedelta(seconds=300))
            if old["accept_until"]
            else now + timedelta(seconds=300)
        )
        run(
            conn,
            "UPDATE credential_versions SET accept_until=:until WHERE tenant_id=:tid AND id=:id",
            until=until,
            id=old["id"],
        )
    identity_audit(
        conn,
        "credential_activated",
        now,
        request_id,
        agent=rid,
        asu=asu["id"],
        version=version,
        operator=operator,
    )
    result = {"credential_version_id": version.hex, "fingerprint": fingerprint}
    if secret:
        result["client_secret"] = secret
    return result


def active_identity(conn, client, version_id, now):
    asu = one(
        conn,
        """SELECT a.*,r.enabled AS registration_enabled FROM agent_system_users a
        JOIN agent_registrations r ON r.tenant_id=:tid AND r.id=a.registration_id
        JOIN accounts u ON u.tenant_id=:tid AND u.id=a.account_id AND NOT u.disabled
        WHERE a.tenant_id=:tid AND a.id=:id""",
        id=client["asu_id"],
    )
    if (
        not asu
        or not asu["enabled"]
        or not asu["registration_enabled"]
        or client["disabled"]
    ):
        raise APIError(401, "INVALID_GRANT")
    try:
        version_id = UUID(str(version_id))
    except (ValueError, TypeError):
        raise APIError(401, "INVALID_GRANT") from None
    version = one(
        conn,
        """SELECT * FROM credential_versions WHERE tenant_id=:tid AND id=:id AND asu_id=:asu
        AND revoked_at IS NULL AND active_from<=:now AND (accept_until IS NULL OR accept_until>:now)
        AND (certificate_expires_at IS NULL OR certificate_expires_at>:now)""",
        id=version_id,
        asu=asu["id"],
        now=now,
    )
    if not version:
        raise APIError(401, "INVALID_GRANT")
    return asu, version


def exchange(conn, tenant, client, form, now, storage):
    if form.grant_type == JWT_BEARER:
        try:
            header = jwt.get_unverified_header(form.assertion or "")
            if header.get("alg") != "RS256":
                raise ValueError()
            version_id = header["kid"]
        except (ValueError, KeyError, jwt.PyJWTError):
            raise APIError(401, "INVALID_GRANT") from None
    else:
        version_id = form.credential_version_id
    asu, version = active_identity(conn, client, version_id, now)
    material = read_credential(storage, tenant, asu, version)
    scopes = set(client["scope_ceiling"])
    grant = None
    if asu["mode"] == "AMBIENT" and form.grant_type == JWT_BEARER:
        account = one(
            conn,
            "SELECT * FROM accounts WHERE tenant_id=:tid AND id=:id",
            id=asu["account_id"],
        )
        try:
            public, fingerprint, _cert_expiry = certificate(
                material["certificate"], now
            )
            if fingerprint != version["fingerprint"]:
                raise ValueError()
            audience = f"https://{tenant['slug']}.mockworkday.local/oauth2/token"
            claims = jwt.decode(
                form.assertion,
                public,
                algorithms=["RS256"],
                issuer=client["client_id"],
                audience=audience,
                options={
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                    "require": ["iss", "sub", "aud", "iat", "exp", "jti"],
                },
            )
            if (
                claims["iss"] != client["client_id"]
                or claims["sub"] != account["username"]
                or claims["aud"] != audience
            ):
                raise ValueError()
            if (
                any(type(claims[k]) is not int for k in ("iat", "exp"))
                or not 0 < claims["exp"] - claims["iat"] <= 60
                or not claims["exp"] > now.timestamp() >= claims["iat"] - 5
            ):
                raise ValueError()
            if "nbf" in claims and (
                type(claims["nbf"]) is not int or claims["nbf"] > now.timestamp()
            ):
                raise ValueError()
            if not isinstance(claims["jti"], str) or not 1 <= len(claims["jti"]) <= 128:
                raise ValueError()
            run(
                conn,
                "DELETE FROM assertion_uses WHERE tenant_id=:tid AND expires_at<=:now",
                now=now,
            )
            inserted = one(
                conn,
                """INSERT INTO assertion_uses VALUES (:tid,:client,:jti,to_timestamp(:expires))
                ON CONFLICT DO NOTHING RETURNING jti""",
                client=client["client_id"],
                jti=claims["jti"],
                expires=claims["exp"],
            )
            if not inserted:
                raise ValueError()
        except (ValueError, TypeError, KeyError, jwt.PyJWTError, APIError):
            raise APIError(401, "INVALID_GRANT") from None
        typ = "ambient"
    elif asu["mode"] == "DELEGATE" and form.grant_type == OBO:
        try:
            valid = check_secret(form.client_secret or "", material["secret_hash"])
        except (ValueError, KeyError):
            valid = False
        if not valid:
            raise APIError(401, "INVALID_CLIENT")
        grant = active_grant(conn, form.grant_id, now)
        if not grant or grant["client_id"] != client["client_id"]:
            raise APIError(401, "INVALID_GRANT")
        account = one(
            conn,
            "SELECT * FROM accounts WHERE tenant_id=:tid AND id=:id AND kind='HUMAN' AND NOT disabled",
            id=grant["user_account_id"],
        )
        if not account:
            raise APIError(401, "INVALID_GRANT")
        scopes &= set(grant["scopes"])
        typ = "delegated"
    else:
        raise APIError(401, "INVALID_GRANT")
    if form.scope is not None:
        requested = set(form.scope.split())
        if not requested <= scopes:
            raise APIError(401, "INVALID_GRANT")
        scopes = requested
    expiry = min(
        [now + timedelta(seconds=300)]
        + [
            x
            for x in (
                version["accept_until"],
                version["certificate_expires_at"],
                grant["expires_at"] if grant else None,
            )
            if x
        ]
    )
    extra = {
        "agent_id": asu["registration_id"].hex,
        "asu_id": asu["id"].hex,
        "credential_version_id": version["id"].hex,
        "operations": sorted(client["allowed_operations"]),
        "exp": int(expiry.timestamp()),
    }
    if grant:
        extra.update(
            gid=grant["id"].hex,
            act={"client_id": client["client_id"], "sub": asu["account_id"].hex},
        )
    return account, typ, sorted(scopes), grant, extra


def seed_registrations(db, references=None):
    from types import SimpleNamespace

    from .clock import SEED_TIME
    from .ids import seed_id

    with db.owner.connect() as conn:
        tenants = rows(conn, "SELECT * FROM tenants ORDER BY slug")
    for tenant in tenants:
        with db.tenant_tx(tenant["id"], owner=True) as conn:
            rid = seed_id(tenant["slug"], "agent_registrations", "core-lab")
            if not one(
                conn,
                "SELECT id FROM agent_registrations WHERE tenant_id=:tid AND id=:id",
                id=rid,
            ):
                create_registration(
                    conn,
                    "core-lab",
                    "Synthetic core integration",
                    SEED_TIME,
                    "seed-identities",
                    rid=rid,
                    operator="bootstrap",
                )
            for mode in ("DELEGATE", "AMBIENT"):
                if one(
                    conn,
                    "SELECT id FROM agent_system_users WHERE tenant_id=:tid AND registration_id=:id AND mode=:mode",
                    id=rid,
                    mode=mode,
                ):
                    continue
                operations = [
                    "list_workers_api_v1_workers_get",
                    "get_worker_api_v1_workers__wid__get",
                ]
                if mode == "DELEGATE":
                    operations += [
                        "get_document_api_v1_documents__wid__get",
                        "list_documents_api_v1_documents_get",
                        "time_off_api_v1_business_processes_request_time_off_post",
                    ]
                group = one(
                    conn,
                    "SELECT id FROM security_groups WHERE tenant_id=:tid AND name='Integration: Directory Reader'",
                )
                body = SimpleNamespace(
                    mode=mode,
                    scopes=["staffing"]
                    if mode == "AMBIENT"
                    else ["staffing", "absence", "documents"],
                    allowed_operations=operations,
                    group_ids=[group["id"]] if mode == "AMBIENT" else [],
                    credential_store_ref=references[tenant["slug"]][mode]
                    if references
                    else None,
                )
                configure_mode(
                    conn,
                    rid,
                    body,
                    SEED_TIME,
                    "seed-identities",
                    seed_slug=tenant["slug"],
                    operator="bootstrap",
                )


def main():
    import argparse
    import json
    from pathlib import Path

    from .config import APP_URL, OWNER_URL
    from .credentials import store_setting
    from .db import Database

    parser = argparse.ArgumentParser(
        description="Seed disabled identities without credentials"
    )
    parser.add_argument(
        "--references",
        type=Path,
        help="AWS tenant slug -> mode -> provisioned secret ARN JSON",
    )
    args = parser.parse_args()
    references = json.loads(args.references.read_text()) if args.references else None
    if store_setting() == "aws" and not references:
        parser.error("--references is required in AWS mode")
    db = Database(APP_URL, OWNER_URL)
    try:
        seed_registrations(db, references)
        print("Seeded disabled identities without credential material.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
