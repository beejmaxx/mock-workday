"""Checkpoint-4 teardown preflight; never delete consumer-owned infrastructure."""

import json
from common import ROOT, aws, deployment


def main():
    config = deployment(optional=True)
    if config is None:
        print(
            "Provider state already empty; continue certificate cleanup and independent leftover inventory."
        )
        return
    connections = aws(
        "ec2",
        "describe-vpc-endpoint-connections",
        "--filters",
        f"Name=service-id,Values={config['endpoint_service_id']}",
    )["VpcEndpointConnections"]
    live = [c for c in connections if c["VpcEndpointState"] != "deleted"]
    if live:
        raise SystemExit(
            "Consumer owner must disconnect these endpoint IDs before provider teardown: "
            + ", ".join(c["VpcEndpointId"] for c in live)
        )
    zone = aws("route53", "get-hosted-zone", "--id", config["private_zone_id"])
    if len(zone.get("VPCs", [])) > 1:
        raise SystemExit(
            "Consumer owner must disassociate its VPC from the private zone before teardown."
        )
    dlqs = {}
    for url in config["dlq_urls"]:
        dlqs[url] = aws(
            "sqs",
            "get-queue-attributes",
            "--queue-url",
            url,
            "--attribute-names",
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
        )["Attributes"]
    (ROOT / ".local/m3-dlq-teardown.json").write_text(json.dumps(dlqs, indent=2) + "\n")
    print("Provider DLQ counts before teardown:", json.dumps(dlqs))
    aws(
        "ecs",
        "update-service",
        "--cluster",
        config["cluster"],
        "--service",
        config["service"],
        "--desired-count",
        "0",
    )
    aws(
        "ecs",
        "wait",
        "services-stable",
        "--cluster",
        config["cluster"],
        "--services",
        config["service"],
    )
    for value in config["tenant_storage"].values():
        bucket = value["bucket"]
        for upload in aws("s3api", "list-multipart-uploads", "--bucket", bucket).get(
            "Uploads", []
        ):
            aws(
                "s3api",
                "abort-multipart-upload",
                "--bucket",
                bucket,
                "--key",
                upload["Key"],
                "--upload-id",
                upload["UploadId"],
            )
    print(
        "API stopped; Terraform owns bucket object/version removal. Consumer resources were not modified."
    )


if __name__ == "__main__":
    main()
