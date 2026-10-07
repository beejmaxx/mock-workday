from typing import Annotated

from fastapi import APIRouter, Form, Request

from ..auth import issue_token, jwks
from .models import Token, TokenInput

router = APIRouter()


@router.post("/oauth2/token", response_model=Token)
def token(request: Request, form: Annotated[TokenInput, Form()]):
    service = request.app.state.service
    tenant = service.tenant(request)
    with service.db.tenant_tx(tenant["id"]) as conn:
        return issue_token(conn, tenant, form, service.clock.now())


@router.get("/.well-known/jwks.json")
def keys(request: Request):
    service = request.app.state.service
    service.tenant(request)
    with service.db.app.connect() as conn:
        return jwks(conn)
