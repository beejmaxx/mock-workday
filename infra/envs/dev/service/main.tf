variable "allowed_cidr" {

  type = string
  validation {

    condition     = can(cidrnetmask(var.allowed_cidr)) && can(regex("/32$", var.allowed_cidr))
    error_message = "allowed_cidr must be one IPv4 address with /32."
  }
}

variable "image_tag" {

  type    = string
  default = "dev"
}

variable "image_digest" {

  type    = string
  default = null
}

data "aws_ssm_parameter" "network" {

  for_each = toset(["vpc_id", "public_subnet_ids", "private_subnet_ids"])
  name     = "/lab/dev/network/${each.key}"
}

module "service" {

  source                      = "../../../modules/service"
  operator_role_arn           = "arn:aws:iam::729608197929:role/managed/AccountFullAccessRole"
  public_domain               = var.public_domain
  allowed_principal           = var.allowed_principal
  consumer_vpc_id             = var.consumer_vpc_id
  consumer_endpoint_id        = var.consumer_endpoint_id
  consumer_endpoint_dns       = var.consumer_endpoint_dns
  target_event_bus_arn        = var.target_event_bus_arn
  event_tenant_slugs          = var.event_tenant_slugs
  private_certificate_arn     = var.private_certificate_arn
  vpc_id                      = nonsensitive(data.aws_ssm_parameter.network["vpc_id"].value)
  public_subnet_ids           = split(",", nonsensitive(data.aws_ssm_parameter.network["public_subnet_ids"].value))
  private_subnet_ids          = split(",", nonsensitive(data.aws_ssm_parameter.network["private_subnet_ids"].value))
  allowed_cidr                = var.allowed_cidr
  image_tag                   = var.image_tag
  image_digest                = var.image_digest
  name                        = "mock-workday-dev"
  region                      = "us-east-2"
  account_id                  = "729608197929"
  repository_name             = "mock-workday"
  db_multi_az                 = false
  backup_retention_days       = 0
  skip_final_snapshot         = true
  deletion_protection         = false
  secret_recovery_window_days = 0
  enable_test_admin           = true
  enable_exec                 = true
  task_cpu                    = 256
  task_memory                 = 512
  db_instance_class           = "db.t4g.micro"
  db_allocated_storage        = 20
  log_retention_days          = 3
}

output "deployment" {

  value = module.service.deployment
}

variable "public_domain" {

  type    = string
  default = null
  validation {

    condition     = var.public_domain == null ? true : can(regex("^[a-z0-9][a-z0-9.-]+\\.[a-z]{2,}$", var.public_domain))
    error_message = "Use a lowercase externally owned domain, without a scheme or wildcard."
  }
}
variable "allowed_principal" {

  type    = string
  default = null
  validation {

    condition     = var.allowed_principal == null ? true : can(regex("^arn:aws:iam::[0-9]{12}:root$", var.allowed_principal))
    error_message = "PrivateLink accepts only an explicit account-root ARN here; role paths are unsupported."
  }
}
variable "consumer_vpc_id" {
  type    = string
  default = null
}
variable "consumer_endpoint_id" {
  type    = string
  default = null
}
variable "consumer_endpoint_dns" {
  type    = string
  default = null
}
variable "target_event_bus_arn" {

  type    = string
  default = null
  validation {

    condition     = var.target_event_bus_arn == null ? true : can(regex("^arn:aws:events:us-east-2:[0-9]{12}:event-bus/[A-Za-z0-9_.-]+$", var.target_event_bus_arn))
    error_message = "The consumer must supply an Ohio event bus ARN."
  }
}
variable "event_tenant_slugs" {
  type    = set(string)
  default = []
  validation {

    condition     = alltrue([for s in var.event_tenant_slugs : contains(["acme", "globex", "northstar", "meridian", "cedar"], s)])
    error_message = "Event subscription tenants must be explicitly selected seeded tenants."
  }
}
variable "private_certificate_arn" {
  type        = string
  default     = null
  description = "Imported lab leaf certificate, supplied outside Terraform; null stages provider resources without the TLS listener."
}
