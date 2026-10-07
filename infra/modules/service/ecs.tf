data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:ecs:${var.region}:${var.account_id}:*"]
    }
  }
}
resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
resource "aws_iam_role_policy" "execution" {
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = local.repository_arn },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.main.arn}:*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = concat([aws_db_instance.main.master_user_secret[0].secret_arn], [for secret in aws_secretsmanager_secret.database : secret.arn]) }
  ] })
}
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
resource "aws_iam_role_policy" "exec" {
  count = var.enable_exec ? 1 : 0
  role  = aws_iam_role.task.id
  # The four documented ECS Exec channel actions; no application AWS permissions.
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow"
    Action   = ["ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel", "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]
    Resource = "*"
  }] })
}
resource "aws_ecs_cluster" "main" {
  name = local.name
}
locals {
  environment = [
    { name = "MW_DB_HOST", value = aws_db_instance.main.address },
    { name = "MW_DB_PORT", value = tostring(aws_db_instance.main.port) },
    { name = "MW_DB_NAME", value = aws_db_instance.main.db_name },
    { name = "MW_TEST_ADMIN", value = var.enable_test_admin ? "1" : "0" }
  ]
  secrets = [
    { name = "MW_DB_OWNER_PASSWORD", valueFrom = aws_secretsmanager_secret.database["owner"].arn },
    { name = "MW_DB_APP_PASSWORD", valueFrom = aws_secretsmanager_secret.database["app"].arn }
  ]
  container = {
    name            = "mock-workday"
    image           = local.image
    essential       = true
    linuxParameters = { initProcessEnabled = true }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.main.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "service"
      }
    }
    environment = local.environment
    secrets     = [for secret in local.secrets : secret if var.enable_test_admin || secret.name != "MW_DB_OWNER_PASSWORD"]
  }
}
resource "aws_ecs_task_definition" "service" {
  family                   = local.name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([merge(local.container, {
    command      = [".venv/bin/python", "-m", "mock_workday.app"]
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
  })])
  depends_on = [aws_iam_role_policy.execution, aws_secretsmanager_secret_version.database]
}
resource "aws_ecs_task_definition" "migration" {
  family                   = "${local.name}-migration"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory)
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([merge(local.container, {
    command = [".venv/bin/python", "-m", "mock_workday.bootstrap"]
    secrets = concat(local.secrets, [
      { name = "MW_DB_ADMIN_PASSWORD", valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::" }
    ])
    environment = concat(local.environment, [{ name = "MW_DB_ADMIN_USER", value = aws_db_instance.main.username }])
  })])
  depends_on = [aws_iam_role_policy.execution, aws_secretsmanager_secret_version.database]
}
resource "aws_ecs_service" "main" {
  name                               = local.name
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.service.arn
  desired_count                      = 1
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  enable_execute_command             = var.enable_exec
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  network_configuration {
    subnets          = var.public_subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.main.arn
    container_name   = "mock-workday"
    container_port   = 8080
  }
  depends_on = [aws_lb_listener.main, aws_iam_role_policy.exec]
}
output "deployment" {
  value = {
    url                       = "http://${aws_lb.main.dns_name}"
    cluster                   = aws_ecs_cluster.main.name
    service                   = aws_ecs_service.main.name
    migration_task_definition = aws_ecs_task_definition.migration.arn
    network                   = { awsvpcConfiguration = { subnets = var.public_subnet_ids, securityGroups = [aws_security_group.task.id], assignPublicIp = "ENABLED" } }
  }
}
