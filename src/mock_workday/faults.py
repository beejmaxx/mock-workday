from threading import Lock
from time import sleep

from .errors import APIError


class Faults:
    def __init__(self):
        self.lock = Lock()
        self.rules = []

    def add(self, tenant_id, rule):
        with self.lock:
            self.rules.append((tenant_id, rule))

    def clear(self, tenant_id=None):
        with self.lock:
            self.rules = [
                (tid, rule)
                for tid, rule in self.rules
                if tenant_id is not None and tid != tenant_id
            ]

    def take(self, tenant_id, method, path, client_id):
        selected = []
        with self.lock:
            for tid, rule in self.rules:
                match = rule["match"]
                if (
                    tid == tenant_id
                    and rule["count"] > 0
                    and match["method"] == method
                    and path.startswith(match["path_prefix"])
                    and (
                        match.get("client_id") is None
                        or match["client_id"] == client_id
                    )
                ):
                    rule["count"] -= 1
                    selected.append(rule.copy())
        return selected


def start_faults(rules, conn):
    for rule in rules:
        if rule["type"] == "latency":
            sleep(rule["value"] / 1000)
        elif rule["type"] == "status":
            status = rule["value"]
            raise APIError(
                status,
                "RATE_LIMITED" if status == 429 else "AUDIT_UNAVAILABLE",
                headers={"Retry-After": "1"} if status == 429 else None,
            )
        elif rule["type"] == "audit_write_failure":
            conn.info["audit_write_failure"] = True


def timeout(rules, when):
    if any(rule["type"] == when for rule in rules):
        raise APIError(504, "SIMULATED_LOST_RESPONSE")
