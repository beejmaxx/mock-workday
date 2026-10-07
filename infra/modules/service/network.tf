data "aws_subnet" "provider" {
  count = length(var.private_subnet_ids)
  id    = var.private_subnet_ids[count.index]
}
resource "aws_security_group" "nlb" {
  name_prefix = "${local.name}-nlb-"
  vpc_id      = var.vpc_id
}
resource "aws_vpc_security_group_egress_rule" "nlb" {
  security_group_id            = aws_security_group.nlb.id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_ingress_rule" "private_task" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.nlb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_lb" "private" {
  name                                                         = "${local.name}-private"
  internal                                                     = true
  load_balancer_type                                           = "network"
  subnets                                                      = var.private_subnet_ids
  security_groups                                              = [aws_security_group.nlb.id]
  enable_cross_zone_load_balancing                             = true
  enforce_security_group_inbound_rules_on_private_link_traffic = "off"
}
resource "aws_lb_target_group" "private" {
  name               = "${local.name}-private"
  port               = 8080
  protocol           = "TCP"
  target_type        = "ip"
  vpc_id             = var.vpc_id
  preserve_client_ip = false
  health_check {
    protocol = "HTTP"
    path     = "/openapi.json"
    port     = "8080"
  }
}
resource "aws_lb_listener" "private" {
  count             = var.private_certificate_arn == null ? 0 : 1
  load_balancer_arn = aws_lb.private.arn
  port              = 443
  protocol          = "TLS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.private_certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.private.arn
  }
}
resource "aws_vpc_endpoint_service" "main" {
  acceptance_required        = true
  network_load_balancer_arns = [aws_lb.private.arn]
}
resource "aws_vpc_endpoint_service_allowed_principal" "runtime" {
  count                   = var.allowed_principal == null ? 0 : 1
  vpc_endpoint_service_id = aws_vpc_endpoint_service.main.id
  principal_arn           = var.allowed_principal
}
resource "aws_vpc_endpoint_connection_accepter" "runtime" {
  count                   = var.consumer_endpoint_id == null ? 0 : 1
  vpc_endpoint_service_id = aws_vpc_endpoint_service.main.id
  vpc_endpoint_id         = var.consumer_endpoint_id
  lifecycle {
    precondition {
      condition     = var.allowed_principal != null
      error_message = "Explicit account permission is required before accepting a named endpoint."
    }
  }
}
resource "aws_route53_zone" "private" {
  name = "mockworkday.internal"
  vpc {
    vpc_id = var.vpc_id
  }
  lifecycle {
    ignore_changes = [vpc]
  }
}
resource "aws_route53_vpc_association_authorization" "runtime" {
  count      = var.consumer_vpc_id == null ? 0 : 1
  zone_id    = aws_route53_zone.private.zone_id
  vpc_id     = var.consumer_vpc_id
  vpc_region = var.region
}
resource "aws_route53_record" "private" {
  for_each = var.consumer_endpoint_dns == null ? {} : local.tenants
  zone_id  = aws_route53_zone.private.zone_id
  name     = "${each.key}.mockworkday.internal"
  type     = "CNAME"
  ttl      = 60
  records  = [var.consumer_endpoint_dns]
  lifecycle {
    precondition {
      condition     = var.consumer_vpc_id != null && var.consumer_endpoint_id != null && var.allowed_principal != null
      error_message = "Private records require an explicitly accepted endpoint and consumer VPC handoff."
    }
  }
}
resource "aws_route53_zone" "public" {
  count = var.public_domain == null ? 0 : 1
  name  = var.public_domain
}
resource "aws_acm_certificate" "public" {
  count             = var.public_domain == null ? 0 : 1
  domain_name       = "*.${var.public_domain}"
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}
resource "aws_route53_record" "validation" {
  for_each = var.public_domain == null ? {} : { wildcard = var.public_domain }
  zone_id  = aws_route53_zone.public[0].zone_id
  name     = one(aws_acm_certificate.public[0].domain_validation_options).resource_record_name
  type     = one(aws_acm_certificate.public[0].domain_validation_options).resource_record_type
  records  = [one(aws_acm_certificate.public[0].domain_validation_options).resource_record_value]
  ttl      = 60
}
resource "aws_acm_certificate_validation" "public" {
  count                   = var.public_domain == null ? 0 : 1
  certificate_arn         = aws_acm_certificate.public[0].arn
  validation_record_fqdns = [aws_route53_record.validation["wildcard"].fqdn]
}
resource "aws_route53_record" "public" {
  for_each = var.public_domain == null ? {} : local.tenants
  zone_id  = aws_route53_zone.public[0].zone_id
  name     = "${each.key}.${var.public_domain}"
  type     = "A"
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}
resource "aws_vpc_security_group_ingress_rule" "https_alb" {
  count             = var.public_domain == null ? 0 : 1
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = var.allowed_cidr
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_lb_listener" "https" {
  count             = var.public_domain == null ? 0 : 1
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.public[0].certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
  }
}
