from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from secrets import token_bytes

from . import audit
from .auth import Principal, authenticate
from .authz import Decision, Target, authorize
from .clock import Clock
from .db import one
from .errors import APIError, request_id
from .ratelimit import RateLimits


@dataclass
class Context:
    service: "Service"
    conn: object
    tenant: object
    p: Principal
    now: datetime
    request_id: str

    def not_found(self, target, *, action="READ"):
        version = one(
            self.conn, "SELECT policy_version FROM tenant_config WHERE tenant_id=:tid"
        )["policy_version"]
        decision = Decision(
            False, "NOT_FOUND", None, None, version, target.job_revision_id
        )
        audit.denial(
            self.service.db,
            self.p,
            self.request_id,
            action,
            None,
            target,
            decision,
            self.now,
        )
        raise APIError(404, "NOT_FOUND")

    def visible_in_list(self, domain, target):
        return authorize(self.conn, self.p, "READ", domain, target, self.now).allowed

    def check(self, action, domain, target, *, sensitive=False):
        decision = authorize(self.conn, self.p, action, domain, target, self.now)
        if not decision.allowed:
            audit.denial(
                self.service.db,
                self.p,
                self.request_id,
                action,
                domain,
                target,
                decision,
                self.now,
            )
        elif action == "WRITE" or sensitive:
            audit.authz_record(
                self.conn,
                self.p,
                self.request_id,
                action,
                domain,
                target,
                decision,
                self.now,
            )
        return decision.allowed

    def require(self, action, domain, target, *, sensitive=False, status=403):
        if not self.check(action, domain, target, sensitive=sensitive):
            raise APIError(status, "NOT_FOUND" if status == 404 else "FORBIDDEN")

    def grant_access(self, action, resource_id=None):
        allowed = self.p.kind == "human"
        version = one(
            self.conn, "SELECT policy_version FROM tenant_config WHERE tenant_id=:tid"
        )["policy_version"]
        decision = Decision(
            allowed,
            "DIRECT_HUMAN" if allowed else "PRINCIPAL_KIND",
            None,
            None,
            version,
            None,
        )
        target = Target("delegation-grants", resource_id)
        if not allowed:
            audit.denial(
                self.service.db,
                self.p,
                self.request_id,
                action,
                None,
                target,
                decision,
                self.now,
            )
            raise APIError(403, "FORBIDDEN")
        if action == "WRITE":
            audit.authz_record(
                self.conn,
                self.p,
                self.request_id,
                action,
                None,
                target,
                decision,
                self.now,
            )


class Service:
    def __init__(self, db, *, test_admin=False):
        self.db = db
        self.clock = Clock(controlled=test_admin)
        self.rate_limits = RateLimits()
        self.cursor_secret = token_bytes(32)

    def tenant(self, request):
        host = request.headers.get("host", "").split(":", 1)[0].lower()
        suffix = ".mockworkday.local"
        slug = host[: -len(suffix)] if host.endswith(suffix) else ""
        with self.db.app.connect() as conn:
            tenant = one(conn, "SELECT * FROM tenants WHERE slug=:slug", slug=slug)
        if not tenant:
            raise APIError(404, "TENANT_NOT_FOUND")
        return tenant

    @contextmanager
    def request(self, request):
        tenant = self.tenant(request)
        now = self.clock.now()
        with self.db.tenant_tx(tenant["id"]) as conn:
            p = authenticate(
                conn, tenant, request.headers.get("authorization", ""), now
            )
            self.rate_limits.check(p, now)
            yield Context(self, conn, tenant, p, now, request_id(request))
