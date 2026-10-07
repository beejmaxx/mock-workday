from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from secrets import token_bytes

from . import audit
from .auth import Principal, authenticate
from .authz import Decision, Target, authorize
from .clock import Clock
from .db import one
from .errors import APIError, request_id
from .faults import Faults, start_faults, timeout
from .ratelimit import RateLimits
from .storage import Storage


@dataclass
class Context:
    service: "Service"
    conn: object
    tenant: object
    p: Principal
    now: datetime
    request_id: str
    uploaded_documents: list = field(default_factory=list)

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
            fail=self.conn.info.pop("audit_write_failure", False),
        )
        raise APIError(404, "NOT_FOUND")

    def visible_in_list(self, domain, target):
        return authorize(self.conn, self.p, "READ", domain, target, self.now).allowed

    def record_decision(self, action, domain, target, decision, *, sensitive=False):
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
                fail=self.conn.info.pop("audit_write_failure", False),
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

    def check(self, action, domain, target, *, sensitive=False):
        decision = authorize(self.conn, self.p, action, domain, target, self.now)
        return self.record_decision(
            action, domain, target, decision, sensitive=sensitive
        )

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
                fail=self.conn.info.pop("audit_write_failure", False),
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
        self.storage = Storage.from_env()
        self.clock = Clock(controlled=test_admin)
        self.rate_limits = RateLimits()
        self.faults = Faults()
        self.cursor_secret = token_bytes(32)

    def tenant(self, request):
        host = request.headers.get("host", "").split(":", 1)[0].lower()
        suffix = ".mockworkday.local"
        slug = host[: -len(suffix)] if host.endswith(suffix) else ""
        with self.db.app.connect() as conn:
            tenant = one(conn, "SELECT * FROM tenants WHERE slug=:slug", slug=slug)
        if not tenant or not tenant["enabled"]:
            raise APIError(404, "TENANT_NOT_FOUND")
        request.state.tenant_id = tenant["id"]
        return tenant

    @contextmanager
    def request(self, request):
        tenant = self.tenant(request)
        now = self.clock.now()
        uploaded = []
        commit_started = False
        try:
            with self.db.tenant_tx(tenant["id"]) as conn:
                p = authenticate(
                    conn,
                    tenant,
                    request.headers.get("authorization", ""),
                    now,
                    self.storage,
                )
                request.state.principal = p
                request.state.tenant_id = tenant["id"]
                if p.asu_id and (
                    request.scope["route"].unique_id not in p.operations
                    or (
                        p.kind == "ambient"
                        and request.method != "GET"
                        and "business-process" in request.url.path
                    )
                ):
                    version = one(
                        conn,
                        "SELECT policy_version FROM tenant_config WHERE tenant_id=:tid",
                    )["policy_version"]
                    audit.denial(
                        self.db,
                        p,
                        request_id(request),
                        "WRITE" if request.method != "GET" else "READ",
                        None,
                        Target("operation", None),
                        Decision(False, "OPERATION_CEILING", None, None, version, None),
                        now,
                    )
                    raise APIError(403, "FORBIDDEN")
                self.rate_limits.check(p, now)
                rules = self.faults.take(
                    tenant["id"], request.method, request.url.path, p.client_id
                )
                info = conn.info
                try:
                    start_faults(rules, conn)
                    yield Context(
                        self, conn, tenant, p, now, request_id(request), uploaded
                    )
                    timeout(rules, "timeout_before_commit")
                    commit_started = True
                finally:
                    info.pop("audit_write_failure", None)
        finally:
            # An uncertain COMMIT outcome may already have durable metadata.
            if not commit_started:
                for document_id in uploaded:
                    self.storage.cleanup(
                        tenant, document_id, request_id=request_id(request)
                    )
        timeout(rules, "timeout_after_commit")
