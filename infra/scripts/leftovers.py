"""Read-only, account-wide inventory; never delete discovered resources."""

import json
import re

from common import aws


def main():
    remaining = []

    tagged = {}
    # D1 permits only Logs inventory in us-east-1; the tag index is Ohio-only.
    for selector in ("Project,Values=mock-workday", "lab,Values=agent-runtime"):
        for item in aws(
            "resourcegroupstaggingapi",
            "get-resources",
            "--tag-filters",
            "Key=" + selector,
        )["ResourceTagMappingList"]:
            tagged[item["ResourceARN"]] = {
                tag["Key"]: tag["Value"] for tag in item.get("Tags", [])
            }
    other_owners = {}

    def owned(identifier, tags=()):
        if isinstance(tags, list):
            tags = {
                t.get("Key", t.get("key")): t.get("Value", t.get("value")) for t in tags
            }
        return dict(tags).get("Project") == "mock-workday" or bool(
            re.search(
                r"(?:^|[:/])(?:mock-workday(?:$|[-/:])|mw-729608197929-)",
                identifier + "/" + dict(tags).get("Name", ""),
            )
        )

    def record(kind, identifier, tags=(), *, region="us-east-2"):
        # The tag index supplies ownership, never proof that a resource still exists.
        indexed = {}
        canonical = identifier
        for arn, values in tagged.items():
            if arn == identifier or (
                (f":{region}:" in arn or ":::" in arn)
                and (
                    arn.endswith(":" + identifier)
                    or arn.endswith("/" + identifier)
                    or arn.endswith(":" + identifier + ":*")
                )
            ):
                indexed.update(values)
                canonical = arn
        values = (
            dict(tags)
            if not isinstance(tags, list)
            else {
                t.get("Key", t.get("key")): t.get("Value", t.get("value")) for t in tags
            }
        )
        indexed.update(values)
        if owned(identifier, indexed):
            remaining.append((kind, identifier))
            print(f"REMAINS {kind}: {identifier}")
        elif indexed.get("lab") == "agent-runtime":
            other_owners[canonical] = indexed

    def is_owned(identifier, tags=()):
        return owned(identifier, tags) or owned(identifier, tagged.get(identifier, {}))

    # Enumerate broadly; fail only on service-owned resources, including untagged names.
    for item in aws("elbv2", "describe-load-balancers")["LoadBalancers"]:
        record(
            "load balancer",
            item["LoadBalancerArn"],
            aws("elbv2", "describe-tags", "--resource-arns", item["LoadBalancerArn"])[
                "TagDescriptions"
            ][0]["Tags"],
        )
    for item in aws("elbv2", "describe-target-groups")["TargetGroups"]:
        record("target group", item["TargetGroupArn"])
    for cluster in aws("ecs", "list-clusters")["clusterArns"]:
        record("ECS cluster", cluster)
        for service in aws("ecs", "list-services", "--cluster", cluster)["serviceArns"]:
            record("ECS service", service)
        task_arns = set()
        # Desired STOPPED can still mean a task is stopping and consuming resources.
        for status in ("RUNNING", "STOPPED"):
            task_arns.update(
                aws(
                    "ecs",
                    "list-tasks",
                    "--cluster",
                    cluster,
                    "--desired-status",
                    status,
                )["taskArns"]
            )
        task_arns = sorted(task_arns)
        for offset in range(0, len(task_arns), 100):
            result = aws(
                "ecs",
                "describe-tasks",
                "--include",
                "TAGS",
                "--cluster",
                cluster,
                "--tasks",
                *task_arns[offset : offset + 100],
            )
            for failure in result.get("failures", []):
                # A task may disappear between listing and describing it.
                if failure.get("reason") != "MISSING":
                    raise SystemExit(f"Could not inspect ECS task: {failure}")
            for task in result["tasks"]:
                if task["lastStatus"] != "STOPPED":
                    record(
                        "ECS task",
                        task["taskArn"],
                        {"Project": "mock-workday"}
                        if is_owned(cluster)
                        or is_owned(task.get("taskDefinitionArn", ""))
                        else task.get("tags", []),
                    )
    for item in aws("rds", "describe-db-instances")["DBInstances"]:
        record("RDS instance", item["DBInstanceIdentifier"], item.get("TagList", []))
    for item in aws("rds", "describe-db-snapshots")["DBSnapshots"]:
        record("RDS snapshot", item["DBSnapshotIdentifier"], item.get("TagList", []))
    for item in aws("rds", "describe-db-instance-automated-backups")[
        "DBInstanceAutomatedBackups"
    ]:
        record(
            "RDS automated backup",
            item["DBInstanceAutomatedBackupsArn"],
            {"Project": "mock-workday"}
            if is_owned(item.get("DBInstanceIdentifier", ""))
            else {},
        )
    for item in aws("ec2", "describe-nat-gateways")["NatGateways"]:
        if item["State"] not in ("deleted", "failed"):
            record("NAT gateway", item["NatGatewayId"], item.get("Tags", []))
    for item in aws("ec2", "describe-addresses")["Addresses"]:
        record("Elastic IP", item["AllocationId"], item.get("Tags", []))
    for item in aws(
        "ec2",
        "describe-network-interfaces",
        "--filters",
        "Name=status,Values=available",
    )["NetworkInterfaces"]:
        record("unattached ENI", item["NetworkInterfaceId"], item.get("TagSet", []))
    for item in aws("ec2", "describe-volumes")["Volumes"]:
        record("EBS volume", item["VolumeId"], item.get("Tags", []))
    for item in aws("secretsmanager", "list-secrets", "--include-planned-deletion")[
        "SecretList"
    ]:
        record("secret (including pending deletion)", item["ARN"], item.get("Tags", []))
    for region in ("us-east-2", "us-east-1"):
        for item in aws("logs", "describe-log-groups", region=region)["logGroups"]:
            record(
                f"log group {region}",
                item["logGroupName"],
                aws(
                    "logs",
                    "list-tags-log-group",
                    "--log-group-name",
                    item["logGroupName"],
                    region=region,
                )["tags"],
                region=region,
            )
    from m3_leftovers import inventory

    inventory(record, is_owned)
    print(
        "OTHER OWNERS (informational; lab=agent-runtime; tags include owner evidence):"
    )
    for arn, tags in tagged.items():
        if tags.get("lab") == "agent-runtime" and not owned(arn, tags):
            other_owners[arn] = tags
    for identifier, tags in sorted(other_owners.items()):
        print("OTHER OWNER:", identifier, json.dumps(tags, sort_keys=True))
    for arn, tags in tagged.items():
        if tags.get("lab") == "agent-runtime" and not owned(arn, tags):
            continue
        if ":ecr:" not in arn and not any(
            identifier == arn for _, identifier in remaining
        ):
            print("WARNING (informational only) tagging API entry:", arn)
    repositories = aws("ecr", "describe-repositories")["repositories"]
    for item in repositories:
        if item["repositoryName"] == "mock-workday":
            print(
                "PERSISTENT (expected): ECR mock-workday; image storage may cost cents/month"
            )
    if remaining:
        raise SystemExit(
            f"{len(remaining)} resource(s) remain owned by Mock Workday; review the inventory."
        )
    print(
        "No disposable/billable leftovers found for Mock Workday. Pending KMS deletions, if any, remain inventoried above. Platform state, network, SSM and registry are intentionally retained."
    )


if __name__ == "__main__":
    main()
