from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class Reference(BaseModel):
    id: str
    descriptor: str
    href: str


class Worker(Reference):
    employeeId: str
    active: bool
    primaryPosition: Reference
    primarySupervisoryOrganization: Reference


class Compensation(BaseModel):
    worker: Reference
    annualSalary: float
    currency: str
    effectiveDate: str


class Organization(Reference):
    refId: str
    superior: Reference | None


class Position(Reference):
    refId: str
    organization: Reference


class DocumentMetadata(Reference):
    title: str
    domain: str
    classification: str
    owner: Reference | None
    org: Reference | None
    created_at: datetime


class Document(DocumentMetadata):
    content: str


class DocumentInput(BaseModel):
    title: str
    content: str
    domain: Literal["DOC_TENANT", "DOC_ORG", "DOC_WORKER"]
    classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    owner_worker_id: UUID | None = None
    org_id: UUID | None = None

    @field_validator("content")
    @classmethod
    def content_size(cls, value):
        if len(value.encode("utf-8")) > 65536:
            raise ValueError("Content must be at most 64 KB")
        return value


class GrantInput(BaseModel):
    client_id: str
    scopes: list[str]
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class Grant(BaseModel):
    id: str
    client_id: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class TokenInput(BaseModel):
    grant_type: str
    username: str | None = None
    password: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    grant_id: str | None = None


class Token(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class WorkerPage(BaseModel):
    data: list[Worker]
    next_cursor: str | None


class DocumentPage(BaseModel):
    data: list[DocumentMetadata]
    next_cursor: str | None


class HistoryItem(BaseModel):
    type: Literal["JOB", "COMPENSATION"]
    effectiveDate: str
    recordedAt: str
    position: Reference | None = None
    annualSalary: float | None = None
    currency: str | None = None
    businessProcessEvent: Reference | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorBody(BaseModel):
    error: ErrorDetail
