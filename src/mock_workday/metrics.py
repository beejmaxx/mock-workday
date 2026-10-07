import json
import logging
from datetime import UTC, datetime

SERVICE_METRICS = {
    "RequestCount": "Count",
    "LatencyMs": "Milliseconds",
    "AuthorizationDenials": "Count",
    "ServerErrors": "Count",
    "BedrockFailures": "Count",
}
TENANT_METRICS = {
    "BedrockInputTokens": "Count",
    "BedrockOutputTokens": "Count",
    "AILimitDenials": "Count",
}
logger = logging.getLogger("mock_workday.metrics")


def emit(values, *, tenant_id=None, request_id=None):
    units = TENANT_METRICS if tenant_id is not None else SERVICE_METRICS
    if not values or not set(values) <= units.keys():
        raise ValueError("Unsupported metric dimensions")
    record = {
        "Service": "mock-workday",
        "Environment": "dev",
        "request_id": request_id,
        **values,
    }
    dimensions = ["Service", "Environment"]
    if tenant_id is not None:
        record["TenantId"] = str(tenant_id)
        dimensions.append("TenantId")
    record["_aws"] = {
        "Timestamp": int(datetime.now(UTC).timestamp() * 1000),
        "CloudWatchMetrics": [
            {
                "Namespace": "MockWorkday",
                "Dimensions": [dimensions],
                "Metrics": [
                    {"Name": name, "Unit": units[name], "StorageResolution": 60}
                    for name in values
                ],
            }
        ],
    }
    logger.info(json.dumps(record))


def request_metrics(status, duration_ms, request_id, *, authorization_denied=False):
    values = {
        "RequestCount": 1,
        "AuthorizationDenials": int(authorization_denied or status in (401, 403)),
        "ServerErrors": int(status >= 500),
    }
    if duration_ms is not None:
        values["LatencyMs"] = max(duration_ms, 0.001)
    emit(values, request_id=request_id)
