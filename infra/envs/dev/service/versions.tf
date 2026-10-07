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
  backend "s3" {
    bucket       = "beejmaxx-lab-tfstate-dev"
    key          = "dev/mock-workday-service.tfstate"
    region       = "us-east-2"
    use_lockfile = true
  }
}

provider "aws" {
  region              = "us-east-2"
  allowed_account_ids = ["729608197929"]
  default_tags {
    tags = {
      Project     = "mock-workday"
      Environment = "dev"
      Stack       = "service"
      ManagedBy   = "terraform"
    }
  }
}
