resource "aws_iam_role" "bootstrap" {
  name               = "${local.name}-bootstrap"
  assume_role_policy = data.aws_iam_policy_document.trust.json
}
resource "aws_iam_role" "tenant_data" {
  name = "${local.name}-tenant-data"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { AWS = [aws_iam_role.task.arn, aws_iam_role.bootstrap.arn] }, Action = ["sts:AssumeRole", "sts:TagSession"],
    Condition = { StringEquals = { "aws:RequestTag/tenant" = values(local.tenants) }, "ForAllValues:StringEquals" = { "aws:TagKeys" = ["tenant"] }, Null = { "aws:RequestTag/tenant" = "false", "aws:TagKeys" = "false" } }
  }] })
}
resource "aws_iam_role_policy" "assume_tenant" {
  for_each = { task = aws_iam_role.task.id, bootstrap = aws_iam_role.bootstrap.id }
  role     = each.value
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["sts:AssumeRole", "sts:TagSession"], Resource = aws_iam_role.tenant_data.arn,
  Condition = { StringEquals = { "aws:RequestTag/tenant" = values(local.tenants) }, "ForAllValues:StringEquals" = { "aws:TagKeys" = ["tenant"] }, Null = { "aws:RequestTag/tenant" = "false", "aws:TagKeys" = "false" } } }] })
}
resource "aws_s3_bucket" "tenant" {
  for_each      = local.tenants
  bucket        = "mw-${var.account_id}-${each.value}"
  force_destroy = true
  tags          = { Tenant = each.value }
}
resource "aws_s3_bucket_public_access_block" "tenant" {
  for_each                = local.tenants
  bucket                  = aws_s3_bucket.tenant[each.key].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "tenant" {
  for_each = local.tenants
  bucket   = aws_s3_bucket.tenant[each.key].id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
resource "aws_kms_key" "tenant" {
  for_each                = local.tenants
  description             = "${local.name} tenant ${each.key} data and credentials"
  deletion_window_in_days = 7
  tags                    = { Tenant = each.value }
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "KeyAdministration", Effect = "Allow", Principal = { AWS = local.root }, Action = ["kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*", "kms:Put*", "kms:Update*", "kms:Revoke*", "kms:Disable*", "kms:Get*", "kms:Delete*", "kms:TagResource", "kms:UntagResource", "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion"], Resource = "*" },
    { Sid = "TaggedS3", Effect = "Allow", Principal = { AWS = aws_iam_role.tenant_data.arn }, Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = "*", Condition = { StringEquals = { "aws:PrincipalTag/tenant" = each.value, "kms:ViaService" = "s3.${var.region}.amazonaws.com", "kms:EncryptionContext:aws:s3:arn" = aws_s3_bucket.tenant[each.key].arn } } },
    { Sid = "TaggedVerification", Effect = "Allow", Principal = { AWS = aws_iam_role.tenant_data.arn }, Action = ["kms:Decrypt"], Resource = "*", Condition = { StringEquals = { "aws:PrincipalTag/tenant" = each.value, "kms:ViaService" = "secretsmanager.${var.region}.amazonaws.com" }, ArnLike = { "kms:EncryptionContext:SecretARN" = "arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:${local.name}/tenants/${each.value}/*" } } },
    { Sid = "OwnerEnrollment", Effect = "Allow", Principal = { AWS = local.root }, Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = "*", Condition = { StringEquals = { "kms:ViaService" = "secretsmanager.${var.region}.amazonaws.com" }, ArnLike = { "kms:EncryptionContext:SecretARN" = "arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:${local.name}/tenants/${each.value}/*" } } }
  ] })
}
resource "aws_kms_alias" "tenant" {
  for_each      = local.tenants
  name          = "alias/${local.name}/${each.key}"
  target_key_id = aws_kms_key.tenant[each.key].key_id
}
resource "aws_s3_bucket_server_side_encryption_configuration" "tenant" {
  for_each = local.tenants
  bucket   = aws_s3_bucket.tenant[each.key].id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.tenant[each.key].arn
    }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "tenant" {
  for_each = local.tenants
  bucket   = aws_s3_bucket.tenant[each.key].id
  rule {
    id     = "expire-exports"
    status = "Enabled"
    filter {
      prefix = "tenants/${each.value}/exports/"
    }
    expiration {
      days = 1
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}
resource "aws_s3_bucket_policy" "tenant" {
  for_each = local.tenants
  bucket   = aws_s3_bucket.tenant[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "RestrictedInventoryAndDeletion", Effect = "Deny", Principal = "*", Action = ["s3:ListBucket", "s3:ListBucketVersions", "s3:ListBucketMultipartUploads", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:AbortMultipartUpload"], Resource = [aws_s3_bucket.tenant[each.key].arn, "${aws_s3_bucket.tenant[each.key].arn}/*"], Condition = { ArnNotEquals = { "aws:PrincipalArn" = [aws_iam_role.tenant_data.arn, var.operator_principal_arn] } } },
    { Sid = "TLSOnly", Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.tenant[each.key].arn, "${aws_s3_bucket.tenant[each.key].arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } },
    { Sid = "ExactKey", Effect = "Deny", Principal = "*", Action = "s3:PutObject", Resource = "${aws_s3_bucket.tenant[each.key].arn}/*", Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = aws_kms_key.tenant[each.key].arn } } },
    { Sid = "KMSOnly", Effect = "Deny", Principal = "*", Action = "s3:PutObject", Resource = "${aws_s3_bucket.tenant[each.key].arn}/*", Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption" = "aws:kms" } } },
    { Sid = "TaggedRoleOnly", Effect = "Deny", Principal = "*", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.tenant[each.key].arn}/*", Condition = { ArnNotEquals = { "aws:PrincipalArn" = aws_iam_role.tenant_data.arn } } },
    { Sid = "MatchingTenant", Effect = "Deny", Principal = "*", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.tenant[each.key].arn}/*", Condition = { StringNotEquals = { "aws:PrincipalTag/tenant" = each.value } } }
  ] })
}
resource "aws_secretsmanager_secret" "asu" {
  for_each                = local.asus
  name_prefix             = "${local.name}/tenants/${each.value.tenant}/${lower(each.value.mode)}-"
  kms_key_id              = aws_kms_key.tenant[each.value.slug].arn
  recovery_window_in_days = 0
  tags                    = { Tenant = each.value.tenant }
}
resource "aws_iam_role_policy" "tenant_data" {
  role = aws_iam_role.tenant_data.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::mw-${var.account_id}-$${aws:PrincipalTag/tenant}", Condition = { StringLike = { "s3:prefix" = ["tenants/$${aws:PrincipalTag/tenant}/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "arn:aws:s3:::mw-${var.account_id}-$${aws:PrincipalTag/tenant}/tenants/$${aws:PrincipalTag/tenant}/*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = "arn:aws:secretsmanager:${var.region}:${var.account_id}:secret:${local.name}/tenants/$${aws:PrincipalTag/tenant}/*" },
    { Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = [for k in aws_kms_key.tenant : k.arn], Condition = { StringEquals = { "aws:ResourceTag/Tenant" = "$${aws:PrincipalTag/tenant}" } } }
  ] })
}
resource "aws_kms_key" "operational" {
  description             = "${local.name} logs, TLS enrollment and event DLQs"
  deletion_window_in_days = 7
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Sid = "OwnerAdministration", Effect = "Allow", Principal = { AWS = local.root }, Action = "kms:*", Resource = "*" },
    { Sid = "EncryptedLogs", Effect = "Allow", Principal = { Service = "logs.${var.region}.amazonaws.com" }, Action = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"], Resource = "*", Condition = { ArnEquals = { "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${var.account_id}:log-group:/ecs/${local.name}" } } }
  ], [for name, bus in local.subscriptions : { Sid = "EventDLQ${name}", Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = ["kms:GenerateDataKey", "kms:Decrypt"], Resource = "*", Condition = { StringEquals = { "aws:SourceAccount" = var.account_id, "kms:ViaService" = "sqs.${var.region}.amazonaws.com" }, ArnEquals = { "aws:SourceArn" = "arn:aws:sqs:${var.region}:${var.account_id}:${local.name}-${name}-dlq" } } }]) })
}
resource "aws_kms_alias" "operational" {
  name          = "alias/${local.name}/operational"
  target_key_id = aws_kms_key.operational.key_id
}
resource "aws_secretsmanager_secret" "tls" {
  name_prefix             = "${local.name}-private-tls-"
  kms_key_id              = aws_kms_key.operational.arn
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_policy" "asu" {
  for_each            = local.asus
  secret_arn          = aws_secretsmanager_secret.asu[each.key].arn
  block_public_policy = true
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Deny", Principal = "*", Action = "secretsmanager:GetSecretValue", Resource = "*", Condition = { ArnNotEquals = { "aws:PrincipalArn" = [aws_iam_role.tenant_data.arn, var.operator_principal_arn] } } },
    { Effect = "Deny", Principal = "*", Action = "secretsmanager:GetSecretValue", Resource = "*", Condition = { ArnEquals = { "aws:PrincipalArn" = aws_iam_role.tenant_data.arn }, StringNotEquals = { "aws:PrincipalTag/tenant" = each.value.tenant } } },
    { Effect = "Deny", Principal = "*", Action = ["secretsmanager:PutSecretValue", "secretsmanager:UpdateSecret", "secretsmanager:UpdateSecretVersionStage"], Resource = "*", Condition = { ArnNotEquals = { "aws:PrincipalArn" = var.operator_principal_arn } } }
  ] })
}
resource "aws_secretsmanager_secret_policy" "tls" {
  secret_arn          = aws_secretsmanager_secret.tls.arn
  block_public_policy = true
  policy              = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Deny", Principal = "*", Action = ["secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue", "secretsmanager:UpdateSecret", "secretsmanager:UpdateSecretVersionStage"], Resource = "*", Condition = { ArnNotEquals = { "aws:PrincipalArn" = var.operator_principal_arn } } }] })
}
