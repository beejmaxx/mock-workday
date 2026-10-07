from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import ai

router = APIRouter(prefix="/api/v1/ai")


class WorkerSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: UUID
    as_of: date | None = None
    include_compensation: bool = False


class TeamSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    org_id: UUID
    as_of: date | None = None
    include_subordinates: bool = False
    include_compensation: bool = False


class DocumentQA(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=65536)
    document_ids: list[UUID] = Field(min_length=1, max_length=8)


class Generate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Literal["mw-small-text-v1"] = "mw-small-text-v1"
    prompt: str = Field(min_length=1, max_length=65536)


class Source(BaseModel):
    resource: Literal["workers", "documents"]
    id: UUID
    fields: list[str]
    source_id: str


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class Answer(BaseModel):
    text: str
    sources: list[Source]
    model: Literal["mw-small-text-v1"]
    invocation_id: UUID | None
    usage: Usage
    request_id: str


@router.post("/worker-summary", response_model=Answer)
def worker_summary(request: Request, body: WorkerSummary):
    return ai.generate(request, "worker-summary", body)


@router.post("/team-summary", response_model=Answer)
def team_summary(request: Request, body: TeamSummary):
    return ai.generate(request, "team-summary", body)


@router.post("/document-qa", response_model=Answer)
def document_qa(request: Request, body: DocumentQA):
    return ai.generate(request, "document-qa", body)


@router.post("/generate", response_model=Answer)
def generate(request: Request, body: Generate):
    return ai.generate(request, "generate", body)
