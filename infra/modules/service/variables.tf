terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

variable "vpc_id" {
  type = string
}
variable "public_subnet_ids" {
  type = list(string)
}
variable "private_subnet_ids" {
  type = list(string)
}
variable "allowed_cidr" {
  type = string
}
variable "image_tag" {
  type = string
}

variable "name" {
  type = string
}
variable "region" {
  type = string
}
variable "account_id" {
  type = string
}
variable "repository_name" {
  type = string
}
variable "image_digest" {
  type    = string
  default = null
}
variable "enable_test_admin" {
  type    = bool
  default = false
}
variable "enable_exec" {
  type    = bool
  default = false
}
variable "task_cpu" {
  type    = number
  default = 256
}
variable "task_memory" {
  type    = number
  default = 512
}
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}
variable "db_allocated_storage" {
  type    = number
  default = 20
}
variable "log_retention_days" {
  type    = number
  default = 3
}

locals {
  name           = var.name
  region         = var.region
  account        = var.account_id
  repository_arn = "arn:aws:ecr:${var.region}:${var.account_id}:repository/${var.repository_name}"
  repository_url = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com/${var.repository_name}"
  image          = var.image_digest == null ? "${local.repository_url}:${var.image_tag}" : "${local.repository_url}@${var.image_digest}"
}

variable "db_multi_az" {
  type    = bool
  default = false
}
variable "backup_retention_days" {
  type    = number
  default = 7
}
variable "skip_final_snapshot" {
  type    = bool
  default = false
}
variable "final_snapshot_identifier" {
  type    = string
  default = null
}
variable "deletion_protection" {
  type    = bool
  default = true
}
variable "secret_recovery_window_days" {
  type    = number
  default = 7
}
