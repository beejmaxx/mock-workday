resource "aws_cloudwatch_event_bus" "main" {
  name = local.name
}
resource "aws_cloudwatch_event_rule" "consumer" {
  for_each       = local.subscriptions
  name           = each.key
  event_bus_name = aws_cloudwatch_event_bus.main.name
  event_pattern  = jsonencode({ source = ["lab.mock-workday"], "detail-type" = ["MockWorkday.BusinessEvent.v1"], detail = { tenant_id = [for s in var.event_tenant_slugs : local.tenants[s]] } })
  lifecycle {
    precondition {
      condition     = length(var.event_tenant_slugs) > 0
      error_message = "A target bus requires a nonempty explicit tenant allowlist."
    }
  }
}
resource "aws_iam_role" "event_sender" {
  for_each           = local.subscriptions
  name               = "${local.name}-events-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = var.account_id }, ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.consumer[each.key].arn } } }] })
}
resource "aws_iam_role_policy" "event_sender" {
  for_each = local.subscriptions
  role     = aws_iam_role.event_sender[each.key].id
  policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "events:PutEvents", Resource = each.value }] })
}
resource "aws_sqs_queue" "dlq" {
  for_each                  = local.subscriptions
  name                      = "${local.name}-${each.key}-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = aws_kms_key.operational.arn
}
resource "aws_sqs_queue_policy" "dlq" {
  for_each  = local.subscriptions
  queue_url = aws_sqs_queue.dlq[each.key].url
  policy    = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sqs:SendMessage", Resource = aws_sqs_queue.dlq[each.key].arn, Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.consumer[each.key].arn }, StringEquals = { "aws:SourceAccount" = var.account_id } } }, { Sid = "OwnerOnlyDrain", Effect = "Deny", Principal = "*", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:PurgeQueue"], Resource = aws_sqs_queue.dlq[each.key].arn, Condition = { ArnNotEquals = { "aws:PrincipalArn" = var.operator_principal_arn } } }] })
}
resource "aws_cloudwatch_event_target" "consumer" {
  for_each       = local.subscriptions
  event_bus_name = aws_cloudwatch_event_bus.main.name
  rule           = aws_cloudwatch_event_rule.consumer[each.key].name
  target_id      = each.key
  arn            = each.value
  role_arn       = aws_iam_role.event_sender[each.key].arn
  retry_policy {
    maximum_event_age_in_seconds = 86400
    maximum_retry_attempts       = 185
  }
  dead_letter_config {
    arn = aws_sqs_queue.dlq[each.key].arn
  }
  depends_on = [aws_sqs_queue_policy.dlq, aws_iam_role_policy.event_sender]
}
resource "aws_iam_role_policy" "application" {
  role = aws_iam_role.task.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "events:PutEvents", Resource = aws_cloudwatch_event_bus.main.arn },
    { Effect = "Allow", Action = "bedrock:InvokeModel", Resource = "arn:aws:bedrock:${var.region}:${var.account_id}:inference-profile/us.amazon.nova-micro-v1:0" },
    { Effect = "Allow", Action = "bedrock:InvokeModel", Resource = [for region in ["us-east-1", "us-east-2", "us-west-2"] : "arn:aws:bedrock:${region}::foundation-model/amazon.nova-micro-v1:0"], Condition = { StringEquals = { "bedrock:InferenceProfileArn" = "arn:aws:bedrock:${var.region}:${var.account_id}:inference-profile/us.amazon.nova-micro-v1:0" } } }
  ] })
}
resource "aws_cloudwatch_event_bus_policy" "producer" {
  event_bus_name = aws_cloudwatch_event_bus.main.name
  policy         = jsonencode({ Version = "2012-10-17", Statement = [{ Sid = "OnlyProviderProducers", Effect = "Deny", Principal = "*", Action = "events:PutEvents", Resource = aws_cloudwatch_event_bus.main.arn, Condition = { ArnNotEquals = { "aws:PrincipalArn" = [aws_iam_role.task.arn, var.operator_principal_arn] } } }] })
}
