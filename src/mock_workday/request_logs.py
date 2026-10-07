import json
import logging
from datetime import UTC, datetime
from time import monotonic

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from .errors import APIError, request_id
from .metrics import request_metrics

logger = logging.getLogger("mock_workday.requests")


def request_log(request: Request, response: Response):
    request.state.log_started = True
    started = monotonic()
    rid = request_id(request)
    response.headers["X-Request-Id"] = rid
    status, error = None, None
    try:
        yield
    except APIError as exc:
        status, error = exc.status, exc.code
        raise
    except RequestValidationError:
        status, error = 422, "VALIDATION_ERROR"
        raise
    except HTTPException as exc:
        status, error = exc.status_code, "HTTP_ERROR"
        raise
    except Exception:  # noqa: BLE001 — redact unexpected SQL/credential failures
        # Convert unexpected failures without logging exception text/SQL parameters.
        status, error = 503, "SERVICE_UNAVAILABLE"
        raise APIError(status, error) from None
    finally:
        route = request.scope.get("route")
        principal = getattr(request.state, "principal", None)
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": "INFO",
            "service": "mock-workday",
            "request_id": rid,
            "tenant_id": str(getattr(request.state, "tenant_id", "")) or None,
            "method": request.method,
            "route": getattr(route, "path", None),
            "status": status
            or getattr(request.state, "response_status", None)
            or response.status_code
            or getattr(route, "status_code", None)
            or 200,
            "duration_ms": round((monotonic() - started) * 1000, 3),
            "error_code": error or getattr(request.state, "error_code", None),
        }
        record.update(getattr(request.state, "identity_context", {}))
        if principal:
            record.update(
                account_id=str(principal.account_id),
                client_id=principal.client_id,
                asu_id=str(principal.asu_id) if principal.asu_id else None,
                by_user_account_id=str(
                    principal.actor_account_id or principal.account_id
                ),
            )
        logger.info(json.dumps(record))
        request_metrics(
            record["status"],
            record["duration_ms"],
            rid,
            authorization_denied=getattr(request.state, "authorization_denied", False),
        )


def early_failure(request, status, code):
    if not getattr(request.state, "log_started", False):
        request_metrics(status, None, request_id(request))
        logger.info(
            json.dumps(
                {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "level": "INFO",
                    "service": "mock-workday",
                    "request_id": request_id(request),
                    "tenant_id": None,
                    "method": request.method,
                    "route": None,
                    "status": status,
                    "duration_ms": None,
                    "error_code": code,
                }
            )
        )
