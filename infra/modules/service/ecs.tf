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
  # The four documented ECS Exec channel actions; application permissions are separate.
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
    { name = "AWS_DEFAULT_REGION", value = var.region },
    { name = "MW_AI_BACKEND", value = "bedrock" },
    { name = "MW_CREDENTIAL_STORE", value = "aws" },
    { name = "MW_EVENT_BUS_ARN", value = aws_cloudwatch_event_bus.main.arn },
    { name = "MW_TENANT_DATA_ROLE_ARN", value = aws_iam_role.tenant_data.arn },
    { name = "MW_TENANT_STORAGE", value = jsonencode({ for slug, tid in local.tenants : tid => { bucket = aws_s3_bucket.tenant[slug].id, kms_key_id = aws_kms_key.tenant[slug].arn } }) },
    { name = "MW_PUBLIC_DOMAIN", value = var.public_domain == null ? "" : var.public_domain },
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
  task_role_arn            = aws_iam_role.bootstrap.arn
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
  dynamic "load_balancer" {
    for_each = var.private_certificate_arn == null ? [] : [1]
    content {
      target_group_arn = aws_lb_target_group.private.arn
      container_name   = "mock-workday"
      container_port   = 8080
    }
  }
  depends_on = [aws_lb_listener.main, aws_lb_listener.private, aws_iam_role_policy.exec, aws_iam_role_policy.application, aws_iam_role_policy.tenant_data, aws_iam_role_policy.assume_tenant, aws_s3_bucket_policy.tenant]
}
output "deployment" {
  value = {
    url                       = var.public_domain == null ? "http://${aws_lb.main.dns_name}" : "https://acme.${var.public_domain}"
    public_domain             = var.public_domain
    private_certificate_arn   = var.private_certificate_arn
    tls_secret_arn            = aws_secretsmanager_secret.tls.arn
    asu_references            = { for slug, tid in local.tenants : slug => { for mode in ["DELEGATE", "AMBIENT"] : mode => aws_secretsmanager_secret.asu["${slug}-${lower(mode)}"].arn } }
    tenant_storage            = { for slug, tid in local.tenants : tid => { bucket = aws_s3_bucket.tenant[slug].id, kms_key_id = aws_kms_key.tenant[slug].arn } }
    event_bus_arn             = aws_cloudwatch_event_bus.main.arn
    event_consumers           = { for name, bus in local.subscriptions : name => { target_bus_arn = bus, sender_role_arn = aws_iam_role.event_sender[name].arn, rule_arn = aws_cloudwatch_event_rule.consumer[name].arn, tenant_ids = [for slug in var.event_tenant_slugs : local.tenants[slug]] } }
    dlq_urls                  = [for q in aws_sqs_queue.dlq : q.url]
    endpoint_service_id       = aws_vpc_endpoint_service.main.id
    endpoint_service_name     = aws_vpc_endpoint_service.main.service_name
    provider_az_ids           = [for s in data.aws_subnet.provider : s.availability_zone_id]
    private_zone_id           = aws_route53_zone.private.zone_id
    private_hosts             = [for slug, tid in local.tenants : "${slug}.mockworkday.internal"]
    public_nameservers        = var.public_domain == null ? [] : aws_route53_zone.public[0].name_servers
    cluster                   = aws_ecs_cluster.main.name
    service                   = aws_ecs_service.main.name
    migration_task_definition = aws_ecs_task_definition.migration.arn
    network                   = { awsvpcConfiguration = { subnets = var.public_subnet_ids, securityGroups = [aws_security_group.task.id], assignPublicIp = "ENABLED" } }
  }
}
