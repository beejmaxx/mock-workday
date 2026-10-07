from common import aws, deployment
import json


def main():
    config = deployment()
    result = aws(
        "ecs",
        "run-task",
        "--cluster",
        config["cluster"],
        "--task-definition",
        config["migration_task_definition"],
        "--launch-type",
        "FARGATE",
        "--platform-version",
        "1.4.0",
        "--network-configuration",
        json.dumps(config["network"]),
        "--tags",
        "key=Project,value=mock-workday",
        "key=Environment,value=dev",
        "key=Stack,value=service",
        "key=ManagedBy,value=terraform",
    )
    if result.get("failures") or len(result.get("tasks", [])) != 1:
        raise SystemExit(f"Migration task failed to start: {result.get('failures')}")
    task = result["tasks"][0]["taskArn"]
    aws("ecs", "wait", "tasks-stopped", "--cluster", config["cluster"], "--tasks", task)
    stopped = aws(
        "ecs", "describe-tasks", "--cluster", config["cluster"], "--tasks", task
    )["tasks"][0]
    if not stopped.get("containers") or any(
        c.get("exitCode") != 0 for c in stopped["containers"]
    ):
        raise SystemExit(
            f"Migration failed: {stopped.get('stoppedReason')}; inspect /ecs/mock-workday-dev logs"
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
    print("Migration succeeded and service is stable:", config["url"])


if __name__ == "__main__":
    main()
