from contextlib import contextmanager
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from .. import identity
from ..credentials import store_setting
from ..db import run
from ..errors import APIError, request_id
from .admin import tenant_id

router = APIRouter(prefix="/admin/agent-registrations")
Mode = Literal["DELEGATE", "AMBIENT"]


class RegistrationInput(BaseModel):
    slug: str
    ref_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)


class RegistrationState(BaseModel):
    slug: str
    enabled: bool


class ModeInput(BaseModel):
    slug: str
    mode: Mode
    scopes: list[str] = Field(default_factory=list)
    allowed_operations: list[str] = Field(default_factory=list)
    group_ids: list[UUID] = Field(default_factory=list)
    credential_store_ref: str | None = Field(default=None, max_length=512)


class ModeState(BaseModel):
    slug: str
    enabled: bool
    scopes: list[str]
    allowed_operations: list[str]


class CredentialInput(BaseModel):
    slug: str
    certificate_pem: str | None = Field(default=None, max_length=16384)
    existing_version: UUID | None = None


class RevokeInput(BaseModel):
    slug: str


@contextmanager
def admin_tx(request, slug):
    service = request.app.state.service
    tid = tenant_id(service, slug)
    request.state.tenant_id = tid
    request.state.identity_context = {"operator": "test-admin"}
    try:
        with service.db.tenant_tx(tid, owner=True) as conn:
            yield (
                conn,
                service,
                {"id": tid, "enabled": True},
                service.clock.now(),
                request_id(request),
            )
    except IntegrityError as exc:
        raise APIError(409, "CONFLICT") from exc


@router.post("", status_code=201)
def create_registration(request: Request, body: RegistrationInput):
    with admin_tx(request, body.slug) as (conn, _service, _tenant, now, rid):
        return identity.create_registration(
            conn, body.ref_id, body.display_name, now, rid
        )


@router.patch("/{registration_id}")
def registration_state(
    request: Request, registration_id: UUID, body: RegistrationState
):
    with admin_tx(request, body.slug) as (conn, _service, _tenant, now, rid):
        identity.registration(conn, registration_id, lock=True)
        run(
            conn,
            "UPDATE agent_registrations SET enabled=:enabled WHERE tenant_id=:tid AND id=:id",
            enabled=body.enabled,
            id=registration_id,
        )
        identity.identity_audit(
            conn,
            "registration_enabled" if body.enabled else "registration_disabled",
            now,
            rid,
            agent=registration_id,
            operator="test-admin",
        )
        return {"id": registration_id.hex, "enabled": body.enabled}


@router.post("/{registration_id}/modes", status_code=201)
def configure_mode(request: Request, registration_id: UUID, body: ModeInput):
    if store_setting() == "aws" and not (body.credential_store_ref or "").startswith(
        "arn:aws:secretsmanager:"
    ):
        raise APIError(422, "VALIDATION_ERROR")
    with admin_tx(request, body.slug) as (conn, _service, _tenant, now, rid):
        return identity.configure_mode(conn, registration_id, body, now, rid)


@router.patch("/{registration_id}/modes/{mode}")
def mode_state(request: Request, registration_id: UUID, mode: Mode, body: ModeState):
    if (
        not set(body.scopes) <= identity.SCOPES
        or not set(body.allowed_operations) <= identity.OPERATIONS
    ):
        raise APIError(422, "VALIDATION_ERROR")
    with admin_tx(request, body.slug) as (conn, _service, _tenant, now, rid):
        asu = identity.asu_row(conn, registration_id, mode, lock=True)
        run(
            conn,
            "UPDATE agent_system_users SET enabled=:enabled WHERE tenant_id=:tid AND id=:id",
            enabled=body.enabled,
            id=asu["id"],
        )
        run(
            conn,
            "UPDATE api_clients SET scope_ceiling=:scopes,allowed_operations=:operations WHERE tenant_id=:tid AND client_id=:cid",
            scopes=sorted(set(body.scopes)),
            operations=sorted(set(body.allowed_operations)),
            cid=asu["client_id"],
        )
        identity.identity_audit(
            conn,
            "asu_configured",
            now,
            rid,
            agent=registration_id,
            asu=asu["id"],
            operator="test-admin",
        )
        return {"id": asu["id"].hex, "enabled": body.enabled}


@router.post("/{registration_id}/modes/{mode}/credentials", status_code=201)
def enroll(request: Request, registration_id: UUID, mode: Mode, body: CredentialInput):
    # The service reads AWS references; an owner process writes secret versions.
    if store_setting() == "aws" and not body.existing_version:
        raise APIError(422, "VALIDATION_ERROR")
    with admin_tx(request, body.slug) as (conn, service, tenant, now, rid):
        return identity.enroll(
            conn,
            tenant,
            service.storage,
            registration_id,
            mode,
            body.certificate_pem,
            now,
            rid,
            existing_version=body.existing_version,
        )


@router.post("/{registration_id}/modes/{mode}/credentials/{version_id}/revoke")
def revoke(
    request: Request,
    registration_id: UUID,
    mode: Mode,
    version_id: UUID,
    body: RevokeInput,
):
    from ..db import one

    with admin_tx(request, body.slug) as (conn, _service, _tenant, now, rid):
        asu = identity.asu_row(conn, registration_id, mode, lock=True)
        version = one(
            conn,
            "SELECT id FROM credential_versions WHERE tenant_id=:tid AND asu_id=:asu AND id=:id",
            asu=asu["id"],
            id=version_id,
        )
        if not version:
            raise APIError(404, "NOT_FOUND")
        run(
            conn,
            "UPDATE credential_versions SET revoked_at=COALESCE(revoked_at,:now) WHERE tenant_id=:tid AND id=:id",
            now=now,
            id=version_id,
        )
        identity.identity_audit(
            conn,
            "credential_revoked",
            now,
            rid,
            agent=registration_id,
            asu=asu["id"],
            version=version_id,
            operator="test-admin",
        )
        reference = asu["credential_store_ref"]
    if store_setting() != "aws":
        import json
        import logging

        from ..credentials import update_local

        try:
            update_local(reference, version_id.hex, None)
        except (OSError, ValueError, TypeError, AttributeError):
            logging.getLogger(__name__).warning(
                json.dumps(
                    {
                        "event": "credential_cleanup_failed",
                        "request_id": rid,
                        "credential_version_id": version_id.hex,
                    }
                )
            )
    return {"credential_version_id": version_id.hex, "revoked": True}
