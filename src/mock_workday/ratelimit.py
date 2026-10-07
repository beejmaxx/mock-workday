import math
from threading import Lock

from .errors import APIError


class RateLimits:
    def __init__(self):
        self.lock = Lock()
        self.limits = {}
        self.buckets = {}

    def reset(self):
        with self.lock:
            self.limits.clear()
            self.buckets.clear()

    def configure(self, tenant, client, capacity, refill):
        with self.lock:
            key = (tenant, client)
            self.limits[key] = (capacity, refill)
            self.buckets.pop(key, None)

    def check(self, p, now):
        key = (p.tenant_id, p.client_id or p.account_id)
        stamp = now.timestamp()
        with self.lock:
            capacity, refill = self.limits.get(key, (100, 50))
            tokens, last = self.buckets.get(key, (capacity, stamp))
            tokens = min(capacity, tokens + max(0, stamp - last) * refill)
            if tokens < 1:
                self.buckets[key] = (tokens, stamp)
                raise APIError(
                    429,
                    "RATE_LIMITED",
                    headers={"Retry-After": str(math.ceil((1 - tokens) / refill))},
                )
            self.buckets[key] = (tokens - 1, stamp)
