from datetime import date, datetime
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


# ISO 4217 current list, SIX (2026-10-07):
# https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml
CURRENCIES = frozenset(
    "AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND BOB BOV BRL BSD BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW UZS VED VES VND VUV WST XAD XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWG".split()
)


class ChangeCompensation(BaseModel):
    annual_salary: float = Field(gt=0, le=9999999999.99, multiple_of=0.01)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value):
        if value not in CURRENCIES:
            raise ValueError("Unknown currency")
        return value


class ChangeJobInput(BaseModel):
    worker_id: UUID
    position_id: UUID
    effective_date: date
    compensation: ChangeCompensation | None = None
    comment: str = ""


class TimeOffInput(BaseModel):
    worker_id: UUID
    start_date: date
    end_date: date
    reason: str


class ActionInput(BaseModel):
    expected_step: str
    expected_version: int
    comment: str = ""


class ProcessStep(BaseModel):
    id: str
    step_order: int
    step_key: str
    status: str
    initial_assignee_account_ids: list[str]
    current_assignee_account_ids: list[str]
    acted_by: str | None
    acted_by_client: str | None
    acted_at: datetime | None
    comment: str | None = None


class ProcessEvent(Reference):
    type: str
    subject_worker_id: str
    initiator_account_id: str
    initiator_client_id: str | None
    status: str
    current_step: int | None
    effective_date: date | None
    payload: dict
    comment: str
    version: int
    initiated_at: datetime
    completed_at: datetime | None
    steps: list[ProcessStep]


class ProcessPage(BaseModel):
    data: list[ProcessEvent]
    next_cursor: str | None


class OperationReceipt(BaseModel):
    operation_id: str
    status: str
    resource: Reference
    event: ProcessEvent | None = None


class TimeOffBalance(BaseModel):
    plan: Literal["VACATION"]
    asOf: date
    grantedHours: float
    takenHours: float
    remainingHours: float


class TimeOffBalances(BaseModel):
    worker: Reference
    data: list[TimeOffBalance]
