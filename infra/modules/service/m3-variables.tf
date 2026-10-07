variable "operator_role_arn" {
  type = string
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
locals {
  tenants = {
    "acme"      = "0de2bbfd-c6df-521a-a5c6-77218a84e572",
    "globex"    = "ec8b3bfc-71e1-5296-8b76-1198189fc69f",
    "northstar" = "73d8c7c2-65ba-5950-8951-463d44e6d95a",
    "meridian"  = "114c8022-ec45-5322-b216-e6f7f3b934df",
    "cedar"     = "76ef9615-ad13-5935-b70e-4f6ba2f421e5"
  }
  root               = "arn:aws:iam::${var.account_id}:root"
  tenant_role_arn    = "arn:aws:iam::${var.account_id}:role/${local.name}-tenant-data"
  bootstrap_role_arn = "arn:aws:iam::${var.account_id}:role/${local.name}-bootstrap"
  subscriptions      = var.target_event_bus_arn == null ? {} : { runtime = var.target_event_bus_arn }
  asus               = { for pair in setproduct(keys(local.tenants), ["DELEGATE", "AMBIENT"]) : "${pair[0]}-${lower(pair[1])}" => { slug = pair[0], mode = pair[1], tenant = local.tenants[pair[0]] } }
}
