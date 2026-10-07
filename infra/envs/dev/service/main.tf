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
