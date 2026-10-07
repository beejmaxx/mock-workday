"""Read-only, account-wide inventory; never delete discovered resources."""

from common import aws


def main():
    remaining = []

    def record(kind, identifier):
        remaining.append((kind, identifier))
        print(f"REMAINS {kind}: {identifier}")

    # Account-wide checks also catch untagged resources created by AWS services.
    for item in aws("elbv2", "describe-load-balancers")["LoadBalancers"]:
        record("load balancer", item["LoadBalancerArn"])
    for item in aws("elbv2", "describe-target-groups")["TargetGroups"]:
        record("target group", item["TargetGroupArn"])
    for cluster in aws("ecs", "list-clusters")["clusterArns"]:
        for service in aws("ecs", "list-services", "--cluster", cluster)["serviceArns"]:
            record("ECS service", service)
        for status in ("RUNNING", "PENDING"):
            for task in aws(
                "ecs", "list-tasks", "--cluster", cluster, "--desired-status", status
            )["taskArns"]:
                record("ECS task", task)
    for item in aws("rds", "describe-db-instances")["DBInstances"]:
        record("RDS instance", item["DBInstanceIdentifier"])
    for item in aws("rds", "describe-db-snapshots")["DBSnapshots"]:
        record("RDS snapshot", item["DBSnapshotIdentifier"])
    for item in aws("rds", "describe-db-instance-automated-backups")[
        "DBInstanceAutomatedBackups"
    ]:
        record("RDS automated backup", item["DBInstanceAutomatedBackupsArn"])
    for item in aws("ec2", "describe-nat-gateways")["NatGateways"]:
        if item["State"] not in ("deleted", "failed"):
            record("NAT gateway", item["NatGatewayId"])
    for item in aws("ec2", "describe-addresses")["Addresses"]:
        record("Elastic IP", item["AllocationId"])
    for item in aws(
        "ec2",
        "describe-network-interfaces",
        "--filters",
        "Name=status,Values=available",
    )["NetworkInterfaces"]:
        record("unattached ENI", item["NetworkInterfaceId"])
    for item in aws("ec2", "describe-volumes")["Volumes"]:
        record("EBS volume", item["VolumeId"])
    for item in aws("secretsmanager", "list-secrets", "--include-planned-deletion")[
        "SecretList"
    ]:
        record("secret (including pending deletion)", item["ARN"])
    for region in ("us-east-2", "us-east-1"):
        for item in aws("logs", "describe-log-groups", region=region)["logGroups"]:
            record(f"log group {region}", item["logGroupName"])
    tagged = aws(
        "resourcegroupstaggingapi",
        "get-resources",
        "--tag-filters",
        "Key=Project,Values=mock-workday",
    )["ResourceTagMappingList"]
    for item in tagged:
        arn = item["ResourceARN"]
        print("TAGGED:", arn)
        if ":ecr:" not in arn and not any(
            identifier == arn for _, identifier in remaining
        ):
            record("project-tagged resource", arn)
    repositories = aws("ecr", "describe-repositories")["repositories"]
    for item in repositories:
        if item["repositoryName"] == "mock-workday":
            print(
                "PERSISTENT (expected): ECR mock-workday; image storage may cost cents/month"
            )
    if remaining:
        raise SystemExit(
            f"{len(remaining)} resource(s) remain; review the inventory (other projects may be included)."
        )
    print(
        "No disposable/billable leftovers found. Platform state, network, SSM and registry are intentionally retained."
    )


if __name__ == "__main__":
    main()
