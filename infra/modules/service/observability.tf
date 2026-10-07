resource "aws_wafv2_web_acl" "public" {
  name  = "${local.name}-public"
  scope = "REGIONAL"
  default_action {
    allow {
    }

  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.name}-public"
    sampled_requests_enabled   = false
  }
  rule {
    name     = "common"
    priority = 10
    override_action {
      none {
      }

    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {
            }

          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-common"
      sampled_requests_enabled   = false
    }
  }
  rule {
    name     = "known-bad-inputs"
    priority = 20
    override_action {
      none {
      }

    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-bad-inputs"
      sampled_requests_enabled   = false
    }
  }
  rule {
    name     = "ip-rate"
    priority = 30
    action {
      block {
      }

    }
    statement {
      rate_based_statement {
        limit                 = 2000
        aggregate_key_type    = "IP"
        evaluation_window_sec = 300
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.name}-ip-rate"
      sampled_requests_enabled   = false
    }
  }
}
resource "aws_wafv2_web_acl_association" "public" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.public.arn
}
locals {
  app_alarms = { latency = { metric = "LatencyMs", threshold = 2000, stat = null, extended = "p95" }, denials = { metric = "AuthorizationDenials", threshold = 20, stat = "Sum", extended = null }, bedrock = { metric = "BedrockFailures", threshold = 0, stat = "Sum", extended = null } }
  dlq_alarms = merge([for name, bus in local.subscriptions : {
    "${name}-visible" = { namespace = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", threshold = 0, dimensions = { QueueName = aws_sqs_queue.dlq[name].name } }
    "${name}-age"     = { namespace = "AWS/SQS", metric = "ApproximateAgeOfOldestMessage", threshold = 3600, dimensions = { QueueName = aws_sqs_queue.dlq[name].name } }
    "${name}-failed"  = { namespace = "AWS/Events", metric = "InvocationsFailedToBeSentToDLQ", threshold = 0, dimensions = { EventBusName = aws_cloudwatch_event_bus.main.name, RuleName = aws_cloudwatch_event_rule.consumer[name].name } }
  }]...)
}
resource "aws_cloudwatch_metric_alarm" "application" {
  for_each            = local.app_alarms
  alarm_name          = "${local.name}-${each.key}"
  namespace           = "MockWorkday"
  metric_name         = each.value.metric
  dimensions          = { Service = "mock-workday", Environment = "dev" }
  period              = 300
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanThreshold"
  threshold           = each.value.threshold
  statistic           = each.value.stat
  extended_statistic  = each.value.extended
  treat_missing_data  = "notBreaching"
  actions_enabled     = false
}
resource "aws_cloudwatch_metric_alarm" "dlq" {
  for_each            = local.dlq_alarms
  alarm_name          = "${local.name}-${each.key}"
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  dimensions          = each.value.dimensions
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = each.value.threshold
  statistic           = "Maximum"
  treat_missing_data  = "notBreaching"
  actions_enabled     = false
}
resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({ widgets = concat([
    { type = "metric", width = 12, height = 6, properties = { title = "API requests and authorization", region = var.region, period = 300, stat = "Sum", metrics = [for m in ["RequestCount", "AuthorizationDenials", "ServerErrors", "BedrockFailures"] : ["MockWorkday", m, "Service", "mock-workday", "Environment", "dev"]] } },
    { type = "metric", width = 12, height = 6, properties = { title = "API p95 latency", region = var.region, period = 300, stat = "p95", metrics = [["MockWorkday", "LatencyMs", "Service", "mock-workday", "Environment", "dev"]] } },
    { type = "metric", width = 12, height = 6, properties = { title = "Tenant tokens and limits", region = var.region, period = 300, stat = "Sum", metrics = flatten([for slug, tid in local.tenants : [for m in ["BedrockInputTokens", "BedrockOutputTokens", "AILimitDenials"] : { metric = ["MockWorkday", m, "Service", "mock-workday", "Environment", "dev", "TenantId", tid] }]])[*].metric } },
    { type = "metric", width = 12, height = 6, properties = { title = "ECS and RDS health", region = var.region, period = 300, metrics = [["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", local.name], ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.identifier], ["AWS/RDS", "FreeableMemory", "DBInstanceIdentifier", aws_db_instance.main.identifier]] } }
  ], [for name, bus in local.subscriptions : { type = "metric", width = 12, height = 6, properties = { title = "${name} delivery and DLQ", region = var.region, period = 300, metrics = [["AWS/Events", "FailedInvocations", "EventBusName", aws_cloudwatch_event_bus.main.name, "RuleName", aws_cloudwatch_event_rule.consumer[name].name], ["AWS/Events", "InvocationsFailedToBeSentToDLQ", "EventBusName", aws_cloudwatch_event_bus.main.name, "RuleName", aws_cloudwatch_event_rule.consumer[name].name], ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.dlq[name].name], ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.dlq[name].name]] } }]) })
}
