import base64
import hashlib
import hmac
import json
from uuid import UUID

from .errors import APIError


def encode64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def decode64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def page(items, ctx, path, filters, limit, cursor, secret):
    query_hash = hashlib.sha256(
        json.dumps([path, filters], sort_keys=True, default=str).encode()
    ).hexdigest()
    binding = {
        "account_id": ctx.p.account_id.hex,
        "client_key": ctx.p.client_id or "-",
        "tenant_id": ctx.p.tenant_id.hex,
        "query_hash": query_hash,
    }
    last = None
    if cursor:
        try:
            payload, signature = cursor.split(".")
            expected = hmac.digest(secret, payload.encode(), "sha256")
            if not hmac.compare_digest(expected, decode64(signature)):
                raise ValueError()
            data = json.loads(decode64(payload))
            if (
                any(data[k] != v for k, v in binding.items())
                or data["exp"] <= ctx.now.timestamp()
            ):
                raise ValueError()
            last = UUID(data["last_id"]).hex
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise APIError(400, "INVALID_CURSOR") from None
    items = sorted(
        (r for r in items if last is None or r["id"] > last), key=lambda r: r["id"]
    )
    visible, rest = items[:limit], items[limit:]
    next_cursor = None
    if rest:
        data = {
            **binding,
            "last_id": visible[-1]["id"],
            "exp": ctx.now.timestamp() + 900,
        }
        payload = encode64(json.dumps(data, sort_keys=True).encode())
        next_cursor = (
            payload + "." + encode64(hmac.digest(secret, payload.encode(), "sha256"))
        )
    return {"data": visible, "next_cursor": next_cursor}
