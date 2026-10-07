import os
import threading

import uvicorn
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPBearer
from starlette.exceptions import HTTPException

from .api import admin, bp, documents, grants, oauth, workers
from .api.models import ErrorBody
from .config import APP_URL, OWNER_URL
from .db import Database
from .errors import APIError, error_response, request_id
from .service import Service


def correlation(request: Request, response: Response):
    response.headers["X-Request-Id"] = request_id(request)


def create_apps(db, *, test_admin=False):
    service = Service(db, test_admin=test_admin)

    def app(title):
        result = FastAPI(
            title=title,
            version="1.0.0",
            dependencies=[Depends(correlation)],
            responses={
                code: {"model": ErrorBody}
                for code in (400, 401, 403, 404, 409, 422, 429, 503, 504)
            },
        )
        result.state.service = service
        result.add_exception_handler(APIError, error_response)

        def invalid(request, exc):
            return error_response(request, APIError(422, "VALIDATION_ERROR"))

        def http_error(request, exc):
            return error_response(
                request,
                APIError(
                    exc.status_code,
                    "NOT_FOUND" if exc.status_code == 404 else "BAD_REQUEST",
                ),
            )

        result.add_exception_handler(RequestValidationError, invalid)
        result.add_exception_handler(HTTPException, http_error)
        return result

    public = app("Mock Workday API")
    public.include_router(oauth.router)
    bearer = HTTPBearer(auto_error=False)
    for router in (grants.router, workers.router, documents.router, bp.router):
        public.include_router(router, dependencies=[Depends(bearer)])
    private = None
    if test_admin:
        private = app("Mock Workday Test Admin")
        private.include_router(admin.router)
    return public, private


def main():
    test_admin = os.getenv("MW_TEST_ADMIN") == "1"
    db = Database(APP_URL, OWNER_URL if test_admin else None)
    public, private = create_apps(db, test_admin=test_admin)
    admin_server = None
    admin_thread = None
    if private:
        admin_server = uvicorn.Server(
            uvicorn.Config(private, host="0.0.0.0", port=8081)
        )
        admin_thread = threading.Thread(target=admin_server.run, daemon=True)
        admin_thread.start()
    try:
        uvicorn.run(public, host="0.0.0.0", port=8080)
    finally:
        if admin_server:
            admin_server.should_exit = True
            admin_thread.join(timeout=10)
        db.close()


if __name__ == "__main__":
    main()
