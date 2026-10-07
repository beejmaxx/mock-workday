import importlib
from pathlib import Path

import pytest


@pytest.fixture
def inventory(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "infra/scripts")
    )
    leftovers = importlib.import_module("leftovers")
    keys = {
        ("s3api", "list-buckets"): "Buckets",
        ("kms", "list-keys"): "Keys",
        ("kms", "list-aliases"): "Aliases",
        ("acm", "list-certificates"): "CertificateSummaryList",
        ("route53", "list-hosted-zones"): "HostedZones",
        (
            "ec2",
            "describe-vpc-endpoint-service-configurations",
        ): "ServiceConfigurations",
        ("events", "list-event-buses"): "EventBuses",
        ("sqs", "list-queues"): "QueueUrls",
        ("cloudwatch", "describe-alarms"): "MetricAlarms",
        ("cloudwatch", "list-dashboards"): "DashboardEntries",
        ("cloudwatch", "list-metrics"): "Metrics",
        ("wafv2", "list-web-acls"): "WebACLs",
        ("iam", "list-roles"): "Roles",
        ("elbv2", "describe-load-balancers"): "LoadBalancers",
        ("elbv2", "describe-target-groups"): "TargetGroups",
        ("ecs", "list-clusters"): "clusterArns",
        ("ecs", "list-services"): "serviceArns",
        ("ecs", "list-tasks"): "taskArns",
        ("ecs", "describe-tasks"): "tasks",
        ("rds", "describe-db-instances"): "DBInstances",
        ("rds", "describe-db-snapshots"): "DBSnapshots",
        ("rds", "describe-db-instance-automated-backups"): "DBInstanceAutomatedBackups",
        ("ec2", "describe-nat-gateways"): "NatGateways",
        ("ec2", "describe-addresses"): "Addresses",
        ("ec2", "describe-network-interfaces"): "NetworkInterfaces",
        ("ec2", "describe-volumes"): "Volumes",
        ("secretsmanager", "list-secrets"): "SecretList",
        ("logs", "describe-log-groups"): "logGroups",
        ("resourcegroupstaggingapi", "get-resources"): "ResourceTagMappingList",
        ("ecr", "describe-repositories"): "repositories",
    }
    responses = {command: {key: []} for command, key in keys.items()}

    def fake_aws(*args, region="us-east-2"):
        assert region in ("us-east-2", "us-east-1")
        return responses[args[:2]]

    monkeypatch.setattr(leftovers, "aws", fake_aws)
    monkeypatch.setattr(importlib.import_module("m3_leftovers"), "aws", fake_aws)
    return leftovers, responses


def test_T_D1_05_stale_inventory_is_informational(inventory, capsys):
    leftovers, responses = inventory
    responses["ecs", "list-clusters"]["clusterArns"] = ["cluster"]
    responses["ecs", "list-services"]["serviceArns"] = ["service"]
    responses["ecs", "list-tasks"]["taskArns"] = ["stopped"]
    responses["ecs", "describe-tasks"]["tasks"] = [
        {"taskArn": "stopped", "lastStatus": "STOPPED"}
    ]
    responses["elbv2", "describe-target-groups"]["TargetGroups"] = [
        {"TargetGroupArn": "target-group"}
    ]
    responses["ec2", "describe-network-interfaces"]["NetworkInterfaces"] = [
        {"NetworkInterfaceId": "eni"}
    ]
    tags = ["deleted-security-group-rule", "inactive-task-definition", "stopped-task"]
    responses["resourcegroupstaggingapi", "get-resources"]["ResourceTagMappingList"] = [
        {"ResourceARN": arn} for arn in tags
    ]
    leftovers.main()
    output = capsys.readouterr().out
    assert "REMAINS" not in output
    for arn in tags:
        assert f"WARNING (informational only) tagging API entry: {arn}" in output
    assert "No disposable/billable leftovers found" in output


@pytest.mark.parametrize("status", ["RUNNING", "PENDING", "STOPPING"])
def test_T_D1_05_live_tasks_fail(inventory, status):
    leftovers, responses = inventory
    responses["ecs", "list-clusters"]["clusterArns"] = ["cluster"]
    responses["ecs", "list-tasks"]["taskArns"] = ["task"]
    responses["ecs", "describe-tasks"]["tasks"] = [
        {
            "taskArn": "task",
            "lastStatus": status,
            "tags": [{"key": "Project", "value": "mock-workday"}],
        }
    ]
    with pytest.raises(SystemExit, match=r"1 resource\(s\) remain"):
        leftovers.main()


def test_T_D1_05_untagged_owned_name_fails(inventory):
    leftovers, responses = inventory
    responses["rds", "describe-db-instances"]["DBInstances"] = [
        {"DBInstanceIdentifier": "mock-workday-dev"}
    ]
    with pytest.raises(SystemExit, match=r"1 resource\(s\) remain"):
        leftovers.main()


@pytest.mark.parametrize("reason", ["MISSING", "ACCESS_DENIED"])
def test_T_D1_05_task_inspection_failure(inventory, reason):
    leftovers, responses = inventory
    responses["ecs", "list-clusters"]["clusterArns"] = ["cluster"]
    responses["ecs", "list-tasks"]["taskArns"] = ["task"]
    responses["ecs", "describe-tasks"]["failures"] = [{"arn": "task", "reason": reason}]
    if reason == "MISSING":
        leftovers.main()
    else:
        with pytest.raises(SystemExit, match="Could not inspect ECS task"):
            leftovers.main()


def test_T_M3_DOWN_01_pending_key_inventory_and_active_key_failure(inventory, capsys):
    leftovers, responses = inventory
    responses["kms", "list-keys"]["Keys"] = [{"KeyId": "tenant-key"}]
    responses["kms", "describe-key"] = {
        "KeyMetadata": {
            "KeyManager": "CUSTOMER",
            "KeyState": "PendingDeletion",
            "Arn": "tenant-key-arn",
            "DeletionDate": "2026-10-15T00:00:00Z",
        }
    }
    responses["kms", "list-resource-tags"] = {
        "Tags": [{"TagKey": "Project", "TagValue": "mock-workday"}]
    }
    leftovers.main()
    assert (
        "PENDING DELETION (nonbillable) tenant-key-arn 2026-10-15"
        in capsys.readouterr().out
    )
    responses["kms", "describe-key"]["KeyMetadata"]["KeyState"] = "Disabled"
    with pytest.raises(SystemExit, match="1 resource"):
        leftovers.main()


def test_T_M3_DOWN_01_unreadable_m3_inventory_cannot_report_clean(inventory):
    leftovers, responses = inventory
    del responses["kms", "list-keys"]
    with pytest.raises(KeyError):
        leftovers.main()


@pytest.mark.parametrize("project, fails", [("s1", False), ("mock-workday", True)])
def test_T_D1_05_owner_scope(inventory, capsys, project, fails):
    leftovers, responses = inventory
    tags = {"lab": "agent-runtime"}
    tags.update({"experiment": "s1"} if project == "s1" else {"Project": project})
    responses["ec2", "describe-addresses"]["Addresses"] = [
        {
            "AllocationId": "eipalloc-test",
            "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
        }
    ]
    if fails:
        with pytest.raises(SystemExit, match="1 resource"):
            leftovers.main()
    else:
        leftovers.main()
    output = capsys.readouterr().out
    if fails:
        assert "REMAINS Elastic IP: eipalloc-test" in output
        assert "OTHER OWNER: eipalloc-test" not in output
    else:
        assert "REMAINS" not in output
        assert "OTHER OWNERS (informational" in output
        assert (
            'OTHER OWNER: eipalloc-test {"experiment": "s1", "lab": "agent-runtime"}'
            in output
        )


def test_T_D1_05_tag_index_requires_live_resource(inventory):
    leftovers, responses = inventory
    arn = "arn:aws:elasticloadbalancing:us-east-2:729608197929:targetgroup/custom/123"
    responses["resourcegroupstaggingapi", "get-resources"]["ResourceTagMappingList"] = [
        {
            "ResourceARN": arn,
            "Tags": [{"Key": "Project", "Value": "mock-workday"}],
        }
    ]
    leftovers.main()
    responses["elbv2", "describe-target-groups"]["TargetGroups"] = [
        {"TargetGroupArn": arn}
    ]
    with pytest.raises(SystemExit, match="1 resource"):
        leftovers.main()


def test_T_D1_05_tag_inventory_error_fails(inventory):
    leftovers, responses = inventory
    del responses["resourcegroupstaggingapi", "get-resources"]
    with pytest.raises(KeyError):
        leftovers.main()


@pytest.mark.parametrize(
    "name, fails", [("mock-workday-dev-orphan", True), ("mock-workdayish", False)]
)
def test_T_D1_05_name_tag_boundary(inventory, name, fails):
    leftovers, responses = inventory
    responses["ec2", "describe-volumes"]["Volumes"] = [
        {
            "VolumeId": "vol-test",
            "Tags": [
                {"Key": "Name", "Value": name},
                {"Key": "lab", "Value": "agent-runtime"},
            ],
        }
    ]
    if fails:
        with pytest.raises(SystemExit, match="1 resource"):
            leftovers.main()
    else:
        leftovers.main()


def test_T_M3_DOWN_01_tagged_bucket_outside_prefix_fails(inventory):
    leftovers, responses = inventory
    responses["s3api", "list-buckets"]["Buckets"] = [{"Name": "custom-bucket"}]
    responses["s3api", "list-object-versions"] = {}
    responses["s3api", "list-multipart-uploads"] = {}
    responses["resourcegroupstaggingapi", "get-resources"]["ResourceTagMappingList"] = [
        {
            "ResourceARN": "arn:aws:s3:::custom-bucket",
            "Tags": [{"Key": "Project", "Value": "mock-workday"}],
        }
    ]
    with pytest.raises(SystemExit, match="1 resource"):
        leftovers.main()


def test_T_D1_05_other_owner_log_tags_and_read_error(inventory, capsys):
    leftovers, responses = inventory
    responses["logs", "describe-log-groups"]["logGroups"] = [
        {"logGroupName": "/lab/s1/dns"}
    ]
    responses["logs", "list-tags-log-group"] = {
        "tags": {"lab": "agent-runtime", "experiment": "s1"}
    }
    leftovers.main()
    assert '"experiment": "s1"' in capsys.readouterr().out
    del responses["logs", "list-tags-log-group"]
    with pytest.raises(KeyError):
        leftovers.main()


def test_T_D1_05_native_other_owner_tags_without_index(inventory, capsys):
    leftovers, responses = inventory
    responses["iam", "list-roles"]["Roles"] = [
        {
            "RoleName": "lab-s1-controller",
            "Arn": "arn:aws:iam::729608197929:role/lab-s1-controller",
        }
    ]
    responses["iam", "list-role-tags"] = {
        "Tags": [
            {"Key": "lab", "Value": "agent-runtime"},
            {"Key": "experiment", "Value": "s1"},
        ]
    }
    leftovers.main()
    output = capsys.readouterr().out
    assert (
        'OTHER OWNER: arn:aws:iam::729608197929:role/lab-s1-controller {"experiment": "s1", "lab": "agent-runtime"}'
        in output
    )
    assert "REMAINS" not in output


@pytest.mark.parametrize(
    "service, action, field, item, kind, identifier",
    [
        (
            "ec2",
            "describe-nat-gateways",
            "NatGateways",
            {"NatGatewayId": "nat-unknown", "State": "available"},
            "NAT gateway",
            "nat-unknown",
        ),
        (
            "ec2",
            "describe-addresses",
            "Addresses",
            {"AllocationId": "eipalloc-unknown"},
            "Elastic IP",
            "eipalloc-unknown",
        ),
        (
            "ec2",
            "describe-volumes",
            "Volumes",
            {"VolumeId": "vol-unknown"},
            "EBS volume",
            "vol-unknown",
        ),
        (
            "rds",
            "describe-db-instances",
            "DBInstances",
            {"DBInstanceIdentifier": "unknown-db"},
            "RDS instance",
            "unknown-db",
        ),
        (
            "secretsmanager",
            "list-secrets",
            "SecretList",
            {"ARN": "unknown-secret"},
            "secret (including pending deletion)",
            "unknown-secret",
        ),
        (
            "elbv2",
            "describe-load-balancers",
            "LoadBalancers",
            {"LoadBalancerArn": "unknown-lb"},
            "load balancer",
            "unknown-lb",
        ),
    ],
)
def test_T_D1_05_untagged_cost_bearing_resource_fails(
    inventory, capsys, service, action, field, item, kind, identifier
):
    leftovers, responses = inventory
    responses[service, action][field] = [item]
    responses["elbv2", "describe-tags"] = {"TagDescriptions": [{"Tags": []}]}
    with pytest.raises(SystemExit, match="UNKNOWN OWNER cost-bearing"):
        leftovers.main()
    output = capsys.readouterr().out
    assert f"UNKNOWN OWNER {kind}: {identifier}" in output
    assert "No disposable/billable leftovers found" not in output
