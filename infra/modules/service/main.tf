resource "aws_security_group" "alb" {
  name_prefix = "${local.name}-alb-"
  vpc_id      = var.vpc_id
}
resource "aws_security_group" "task" {
  name_prefix = "${local.name}-task-"
  vpc_id      = var.vpc_id
}
resource "aws_security_group" "db" {
  name_prefix = "${local.name}-db-"
  vpc_id      = var.vpc_id
}
resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = var.allowed_cidr
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}
resource "aws_vpc_security_group_egress_rule" "alb" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_ingress_rule" "task" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.task.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_egress_rule" "postgres" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_vpc_security_group_ingress_rule" "postgres" {
  security_group_id            = aws_security_group.db.id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = var.private_subnet_ids
}
resource "aws_db_instance" "main" {
  identifier                  = local.name
  engine                      = "postgres"
  engine_version              = "17"
  instance_class              = var.db_instance_class
  allocated_storage           = var.db_allocated_storage
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = "mock_workday"
  username                    = "mw_admin"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.main.name
  vpc_security_group_ids      = [aws_security_group.db.id]
  publicly_accessible         = false
  multi_az                    = var.db_multi_az
  backup_retention_period     = var.backup_retention_days
  skip_final_snapshot         = var.skip_final_snapshot
  deletion_protection         = var.deletion_protection
  final_snapshot_identifier   = var.final_snapshot_identifier
  copy_tags_to_snapshot       = true
  delete_automated_backups    = true
}
resource "random_password" "database" {
  for_each = toset(["owner", "app"])
  length   = 32
  special  = false
}
resource "aws_secretsmanager_secret" "database" {
  for_each                = random_password.database
  name_prefix             = "${local.name}-${each.key}-"
  recovery_window_in_days = var.secret_recovery_window_days
}
resource "aws_secretsmanager_secret_version" "database" {
  for_each      = random_password.database
  secret_id     = aws_secretsmanager_secret.database[each.key].id
  secret_string = each.value.result
}
resource "aws_cloudwatch_log_group" "main" {
  kms_key_id = aws_kms_key.operational.arn

  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
}
resource "aws_lb" "main" {
  name               = local.name
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
}
resource "aws_lb_target_group" "main" {
  name        = local.name
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = var.vpc_id
  health_check {
    path    = "/openapi.json"
    matcher = "200"
  }
}
resource "aws_lb_listener" "main" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"
  dynamic "default_action" {
    for_each = var.public_domain == null ? [1] : []
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.main.arn
    }
  }
  dynamic "default_action" {
    for_each = var.public_domain == null ? [] : [1]
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }
}
