terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  cloud {
    organization = "tu-organizacion-en-tfc"

    workspaces {
      name = "portfolio-diego"
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