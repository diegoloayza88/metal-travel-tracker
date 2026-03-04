terraform {
  required_version = ">= 1.14.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.17.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "2.7.1"
    }
  }

  cloud {
    organization = "portfolio-diego"

    workspaces {
      name = "metal-travel-tracker"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "metal-travel-tracker"
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = "diego"
    }
  }
}