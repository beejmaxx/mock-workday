import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .errors import APIError

SDK_CONFIG = Config(
    retries={"total_max_attempts": 2, "mode": "standard"},
    connect_timeout=3,
    read_timeout=10,
)


def fingerprint(content):
    body = content.encode("utf-8")
    return len(body), hashlib.sha256(body).hexdigest()


class Storage:
    def __init__(self, mapping=None, role_arn=None):
        self.mapping = {str(UUID(tid)): value for tid, value in (mapping or {}).items()}
        self.role_arn = role_arn
        if bool(self.mapping) != bool(role_arn):
            raise ValueError(
                "Tenant storage and tenant data role must be configured together"
            )
        for value in self.mapping.values():
            if set(value) != {"bucket", "kms_key_id"} or not all(value.values()):
                raise ValueError("Tenant storage requires bucket and kms_key_id")
        self.locks = {tid: Lock() for tid in self.mapping}
        self.sessions = {}
        self.aws_sessions = {}
        self.secret_clients = {}
        self.sts = None
        self.sts_lock = Lock()

    @classmethod
    def from_env(cls):
        return cls(
            json.loads(os.getenv("MW_TENANT_STORAGE", "{}")),
            os.getenv("MW_TENANT_DATA_ROLE_ARN"),
        )

    def client(self, tenant):
        tid = str(tenant["id"])
        if not tenant["enabled"] or tid not in self.mapping:
            raise APIError(503, "SERVICE_UNAVAILABLE")
        key = (self.role_arn, tid)
        with self.locks[tid]:
            now = datetime.now(UTC)
            cached = self.sessions.get(key)
            if cached and (cached[0] - now).total_seconds() > 120:
                return cached[1]
            with self.sts_lock:
                if self.sts is None:
                    self.sts = boto3.session.Session().client("sts", config=SDK_CONFIG)
            credentials = self.sts.assume_role(
                RoleArn=self.role_arn,
                RoleSessionName="mw-" + tid,
                DurationSeconds=900,
                Tags=[{"Key": "tenant", "Value": tid}],
            )["Credentials"]
            session = boto3.session.Session(
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            )
            client = session.client("s3", config=SDK_CONFIG)
            self.aws_sessions[key] = session
            self.secret_clients.pop(key, None)
            self.sessions[key] = (credentials["Expiration"], client)
            return client

    def secret_client(self, tenant):
        self.client(tenant)
        tid = str(tenant["id"])
        key = (self.role_arn, tid)
        with self.locks[tid]:
            if key not in self.secret_clients:
                self.secret_clients[key] = self.aws_sessions[key].client(
                    "secretsmanager", config=SDK_CONFIG
                )
            return self.secret_clients[key]

    def location(self, tenant, document_id):
        tid = str(tenant["id"])
        if not tenant["enabled"] or tid not in self.mapping:
            raise APIError(503, "SERVICE_UNAVAILABLE")
        return self.mapping[tid], f"tenants/{tid}/documents/{UUID(str(document_id))}"

    def put(self, tenant, document_id, content, *, reuse=False):
        size, digest = fingerprint(content)
        if not self.mapping:
            return {"content": content, "content_bytes": size, "content_sha256": digest}
        try:
            destination, key = self.location(tenant, document_id)
            self.client(tenant).put_object(
                Bucket=destination["bucket"],
                Key=key,
                Body=content.encode("utf-8"),
                ContentType="text/plain; charset=utf-8",
                IfNoneMatch="*",
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=destination["kms_key_id"],
                BucketKeyEnabled=True,
                Metadata={"sha256": digest},
            )
        except ClientError as exc:
            if reuse and exc.response["Error"]["Code"] in ("PreconditionFailed", "412"):
                self.get(
                    tenant,
                    {
                        "id": document_id,
                        "content_bytes": size,
                        "content_sha256": digest,
                    },
                )
            else:
                raise APIError(503, "SERVICE_UNAVAILABLE") from exc
        except BotoCoreError as exc:
            raise APIError(503, "SERVICE_UNAVAILABLE") from exc
        return {"content": None, "content_bytes": size, "content_sha256": digest}

    def get(self, tenant, document):
        if not self.mapping:
            if document["content"] is None:
                raise APIError(503, "SERVICE_UNAVAILABLE")
            if document["content_bytes"] is not None and fingerprint(
                document["content"]
            ) != (document["content_bytes"], document["content_sha256"]):
                raise APIError(503, "SERVICE_UNAVAILABLE")
            return document["content"]
        try:
            destination, key = self.location(tenant, document["id"])
            result = self.client(tenant).get_object(
                Bucket=destination["bucket"], Key=key
            )
            with result["Body"] as stream:
                body = stream.read(65537)
            if (
                len(body) != document["content_bytes"]
                or hashlib.sha256(body).hexdigest() != document["content_sha256"]
            ):
                raise APIError(503, "SERVICE_UNAVAILABLE")
            return body.decode("utf-8")
        except (BotoCoreError, ClientError, UnicodeDecodeError) as exc:
            raise APIError(503, "SERVICE_UNAVAILABLE") from exc

    def cleanup(self, tenant, document_id, *, request_id=None):
        if not self.mapping:
            return
        try:
            destination, key = self.location(tenant, document_id)
            self.client(tenant).delete_object(Bucket=destination["bucket"], Key=key)
        except (BotoCoreError, ClientError, APIError):
            # No SDK exception text: it may contain sensitive request parameters.
            logging.getLogger(__name__).warning(
                json.dumps(
                    {
                        "event": "document_orphan",
                        "X-Request-Id": request_id,
                        "tenant_id": str(tenant["id"]),
                        "document_id": str(document_id),
                    }
                )
            )


def orphan_page(db, storage, tenant_id, *, starting_token=None, delete=False):
    from .db import one, rows, run

    tid = UUID(str(tenant_id))
    with db.owner.connect() as lock:
        acquired = one(
            lock,
            "SELECT pg_try_advisory_lock(hashtextextended(:key,0)) AS acquired",
            key=f"bulk:{tid}",
        )["acquired"]
        lock.commit()
        if not acquired:
            raise ValueError(
                "Tenant import is running; retry cleanup after it finishes"
            )
        try:
            tenant = one(lock, "SELECT * FROM tenants WHERE id=:id", id=tid)
            if not tenant:
                raise ValueError("Unknown tenant")
            # This owner-only command must also inventory failed, disabled imports.
            tenant = dict(tenant) | {"enabled": True}
            destination, _ = storage.location(tenant, UUID(int=0))
            client = storage.client(tenant)
            config = {"MaxItems": 1000, "PageSize": 1000}
            if starting_token:
                config["StartingToken"] = starting_token
            page = (
                client.get_paginator("list_objects_v2")
                .paginate(
                    Bucket=destination["bucket"],
                    Prefix=f"tenants/{tid}/documents/",
                    PaginationConfig=config,
                )
                .build_full_result()
            )
            candidates = []
            for item in page.get("Contents", []):
                try:
                    did = UUID(item["Key"].rsplit("/", 1)[-1])
                except ValueError:
                    continue
                # Avoid collecting an in-flight API upload before its metadata commits.
                if (datetime.now(UTC) - item["LastModified"]).total_seconds() >= 86400:
                    candidates.append((did, item["Key"]))
            with db.tenant_tx(tid, owner=True) as conn:
                referenced = {
                    r["id"]
                    for r in rows(
                        conn,
                        "SELECT id FROM documents WHERE tenant_id=:tid AND id=ANY(:ids)",
                        ids=[did for did, _ in candidates],
                    )
                }
            orphan_keys = [key for did, key in candidates if did not in referenced]
            if delete:
                for key in orphan_keys:
                    client.delete_object(Bucket=destination["bucket"], Key=key)
            return {
                "tenant_id": str(tid),
                "orphan_keys": orphan_keys,
                "deleted": delete,
                "next_token": page.get("NextToken"),
            }
        finally:
            run(
                lock,
                "SELECT pg_advisory_unlock(hashtextextended(:key,0))",
                key=f"bulk:{tid}",
            )
            lock.commit()


def main():
    import argparse

    from .config import APP_URL, OWNER_URL
    from .db import Database

    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True, type=UUID)
    parser.add_argument("--starting-token")
    parser.add_argument("--delete-orphans", action="store_true")
    args = parser.parse_args()
    db = Database(APP_URL, OWNER_URL)
    try:
        print(
            json.dumps(
                orphan_page(
                    db,
                    Storage.from_env(),
                    args.tenant,
                    starting_token=args.starting_token,
                    delete=args.delete_orphans,
                )
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
