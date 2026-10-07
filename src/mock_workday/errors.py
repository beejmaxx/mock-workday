from uuid import uuid4

from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, status, code, message=None, headers=None):
        self.status = status
        self.code = code
        self.message = message or code.replace("_", " ").capitalize()
        self.headers = headers or {}


def request_id(request):
    if not hasattr(request.state, "request_id"):
        request.state.request_id = request.headers.get("X-Request-Id") or uuid4().hex
    return request.state.request_id


def error_response(request, exc):
    rid = request_id(request)
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message, "request_id": rid}},
        status_code=exc.status,
        headers={"X-Request-Id": rid, **exc.headers},
    )
