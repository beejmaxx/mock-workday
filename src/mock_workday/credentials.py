import fcntl
import json
import os
import stat
import tempfile
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .errors import APIError
from .storage import SDK_CONFIG


def store_setting():
    return os.getenv(
        "MW_CREDENTIAL_STORE", "/tmp/mock-workday-credentials/credentials.json"
    )


def local_read(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW)) as file:
        metadata = os.fstat(file.fileno())
        if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_uid != os.getuid():
            raise ValueError("Credential document must be private")
        contents = json.load(file)
        if not isinstance(contents, dict) or any(
            not isinstance(versions, dict) for versions in contents.values()
        ):
            raise ValueError("Invalid credential document")
        return contents


def read_credential(storage, tenant, asu, version):
    try:
        if store_setting() == "aws":
            result = storage.secret_client(tenant).get_secret_value(
                SecretId=asu["credential_store_ref"], VersionId=version["store_version"]
            )
            if result["VersionId"] != version["store_version"]:
                raise ValueError("Wrong credential version")
            value = json.loads(result["SecretString"])
        else:
            value = local_read(store_setting())[asu["credential_store_ref"]][
                version["store_version"]
            ]
        if (
            value["tenant_id"] != str(tenant["id"])
            or value["asu_id"] != str(asu["id"])
            or value["mode"] != asu["mode"]
        ):
            raise ValueError("Credential binding mismatch")
        return value
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        BotoCoreError,
        ClientError,
    ) as exc:
        raise APIError(503, "SERVICE_UNAVAILABLE") from exc


def write_credential(reference, version, value):
    # Called only by local test-admin or an owner enrollment process, never public auth.
    try:
        if store_setting() == "aws":
            boto3.session.Session().client(
                "secretsmanager", config=SDK_CONFIG
            ).put_secret_value(
                SecretId=reference,
                ClientRequestToken=version,
                SecretString=json.dumps(value),
            )
            return
        update_local(reference, version, value)
    except (OSError, ValueError, BotoCoreError, ClientError) as exc:
        raise APIError(503, "SERVICE_UNAVAILABLE") from exc


def update_local(reference, version, value):
    path = Path(store_setting())
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = str(path) + ".lock"
    with os.fdopen(
        os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w"
    ) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        contents = local_read(path) if path.exists() else {}
        versions = contents.setdefault(reference, {})
        if value is None:
            versions.pop(version, None)
        else:
            if version in versions:
                raise ValueError("Immutable credential version already exists")
            versions[version] = value
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(contents, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def main():
    import argparse
    import secrets
    from datetime import UTC, datetime
    from uuid import UUID, uuid4

    from .auth import hash_secret
    from .identity import certificate

    parser = argparse.ArgumentParser(
        description="Owner-only credential preparation; activation is a separate audited admin API call"
    )
    parser.add_argument("--tenant", required=True, type=UUID)
    parser.add_argument("--asu", required=True, type=UUID)
    parser.add_argument("--mode", required=True, choices=("AMBIENT", "DELEGATE"))
    parser.add_argument("--reference", required=True)
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    version = uuid4().hex
    value = {"tenant_id": str(args.tenant), "asu_id": str(args.asu), "mode": args.mode}
    result = {"credential_version_id": version}
    if args.mode == "AMBIENT":
        if not args.certificate:
            parser.error("--certificate is required for ambient credentials")
        pem = args.certificate.read_text()
        certificate(pem, datetime.now(UTC))
        value["certificate"] = pem
    else:
        secret = secrets.token_urlsafe(32)
        value["secret_hash"] = hash_secret(secret)
        result["client_secret"] = secret
    # Refuse overwrite before making a cloud write; an interrupted enrollment is rotated.
    with os.fdopen(
        os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as output:
        write_credential(args.reference, version, value)
        json.dump(result, output)
    print("Credential prepared; activate its version through the isolated admin API.")


if __name__ == "__main__":
    main()
