"""Service-owned M3 inventory; errors propagate, pending key deletion is explicit."""

from common import aws

PREFIX = "mock-workday-dev"


def inventory(record):
    for bucket in aws("s3api", "list-buckets")["Buckets"]:
        name = bucket["Name"]
        if name.startswith("mw-729608197929-"):
            record("tenant bucket", name)
            for action, field in [
                ("list-object-versions", "Versions"),
                ("list-multipart-uploads", "Uploads"),
            ]:
                result = aws("s3api", action, "--bucket", name)
                print(
                    "INFO bucket contents",
                    name,
                    field,
                    len(result.get(field, [])),
                    "delete markers",
                    len(result.get("DeleteMarkers", [])),
                )
    for item in aws("kms", "list-keys")["Keys"]:
        key = aws("kms", "describe-key", "--key-id", item["KeyId"])["KeyMetadata"]
        if key["KeyManager"] != "CUSTOMER":
            continue
        tags = aws("kms", "list-resource-tags", "--key-id", item["KeyId"])["Tags"]
        if not any(
            t["TagKey"] == "Project" and t["TagValue"] == "mock-workday" for t in tags
        ):
            continue
        if key["KeyState"] == "PendingDeletion":
            print("PENDING DELETION (nonbillable)", key["Arn"], key["DeletionDate"])
        else:
            record("KMS key", key["Arn"])
    for alias in aws("kms", "list-aliases")["Aliases"]:
        if alias["AliasName"].startswith("alias/" + PREFIX):
            record("KMS alias", alias["AliasName"])
    for cert in aws("acm", "list-certificates")["CertificateSummaryList"]:
        tags = aws(
            "acm",
            "list-tags-for-certificate",
            "--certificate-arn",
            cert["CertificateArn"],
        )["Tags"]
        if any(t["Key"] == "Project" and t["Value"] == "mock-workday" for t in tags):
            record("ACM certificate", cert["CertificateArn"])
    for zone in aws("route53", "list-hosted-zones")["HostedZones"]:
        tags = aws(
            "route53",
            "list-tags-for-resource",
            "--resource-type",
            "hostedzone",
            "--resource-id",
            zone["Id"].split("/")[-1],
        )["ResourceTagSet"]["Tags"]
        if any(t["Key"] == "Project" and t["Value"] == "mock-workday" for t in tags):
            record("hosted zone", zone["Id"])
            print(
                "INFO zone associations",
                aws("route53", "get-hosted-zone", "--id", zone["Id"]).get("VPCs", []),
            )
    for service in aws("ec2", "describe-vpc-endpoint-service-configurations")[
        "ServiceConfigurations"
    ]:
        if any(
            t["Key"] == "Project" and t["Value"] == "mock-workday"
            for t in service.get("Tags", [])
        ):
            record("endpoint service", service["ServiceId"])
            for connection in aws(
                "ec2",
                "describe-vpc-endpoint-connections",
                "--filters",
                f"Name=service-id,Values={service['ServiceId']}",
            )["VpcEndpointConnections"]:
                record(
                    "consumer endpoint connection (other owner)",
                    connection["VpcEndpointId"],
                )
    for bus in aws("events", "list-event-buses", "--name-prefix", PREFIX)["EventBuses"]:
        record("event bus", bus["Arn"])
        for rule in aws("events", "list-rules", "--event-bus-name", bus["Name"])[
            "Rules"
        ]:
            record("event rule", rule["Arn"])
            print(
                "INFO event targets",
                aws(
                    "events",
                    "list-targets-by-rule",
                    "--event-bus-name",
                    bus["Name"],
                    "--rule",
                    rule["Name"],
                )["Targets"],
            )
    for queue in aws("sqs", "list-queues", "--queue-name-prefix", PREFIX).get(
        "QueueUrls", []
    ):
        record("provider DLQ", queue)
        print(
            "INFO DLQ",
            aws(
                "sqs",
                "get-queue-attributes",
                "--queue-url",
                queue,
                "--attribute-names",
                "ApproximateNumberOfMessages",
            )["Attributes"],
        )
    for alarm in aws("cloudwatch", "describe-alarms", "--alarm-name-prefix", PREFIX)[
        "MetricAlarms"
    ]:
        record("alarm", alarm["AlarmArn"])
    for dashboard in aws(
        "cloudwatch", "list-dashboards", "--dashboard-name-prefix", PREFIX
    )["DashboardEntries"]:
        record("dashboard", dashboard["DashboardArn"])
    for acl in aws("wafv2", "list-web-acls", "--scope", "REGIONAL")["WebACLs"]:
        if acl["Name"].startswith(PREFIX):
            record("WAF ACL", acl["ARN"])
            print(
                "INFO WAF associations",
                aws("wafv2", "list-resources-for-web-acl", "--web-acl-arn", acl["ARN"])[
                    "ResourceArns"
                ],
            )
    for role in aws("iam", "list-roles")["Roles"]:
        if role["RoleName"].startswith(PREFIX):
            record("service IAM role", role["Arn"])
    metrics = aws("cloudwatch", "list-metrics", "--namespace", "MockWorkday")["Metrics"]
    print(
        "INFO historical EMF series (cannot delete; not evidence of active publishing):",
        len(metrics),
    )
