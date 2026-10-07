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
        {"taskArn": "task", "lastStatus": status}
    ]
    with pytest.raises(SystemExit, match=r"1 resource\(s\) remain"):
        leftovers.main()


def test_T_D1_05_untagged_billable_resource_fails(inventory):
    leftovers, responses = inventory
    responses["rds", "describe-db-instances"]["DBInstances"] = [
        {"DBInstanceIdentifier": "database"}
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
