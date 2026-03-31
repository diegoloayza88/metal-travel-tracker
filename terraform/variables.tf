###############################################################################
# terraform/variables.tf
# All project variables. Sensitive values (API keys) are passed via
# terraform.tfvars (git-ignored) or from AWS Secrets Manager.
###############################################################################

# ---- General ----------------------------------------------------------------

variable "aws_region" {
  description = "AWS region where the project will be deployed"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "project_name" {
  description = "Base project name for naming resources"
  type        = string
  default     = "metal-travel-tracker"
}

# ---- Notifications ----------------------------------------------------------

variable "notification_phone_number" {
  description = "Phone number for SMS in E.164 format (e.g., +51999999999)"
  type        = string
  sensitive   = true
}

variable "notification_email_from" {
  description = "SES-verified sender email (e.g., your_email@gmail.com)"
  type        = string
  sensitive   = true
}

variable "notification_email_to" {
  description = "Destination email for reports"
  type        = string
  sensitive   = true
}

variable "discord_webhook_url" {
  description = "Discord webhook URL for notifications"
  type        = string
  sensitive   = true
}

# ---- Concert APIs -----------------------------------------------------------

variable "ticketmaster_api_key" {
  description = "Ticketmaster Discovery API Key (gratuita en developer.ticketmaster.com)"
  type        = string
  sensitive   = true
  default     = ""
}

# Variables legacy mantenidas para compatibilidad (no se usan en plugins activos)
variable "songkick_api_key" {
  description = "Songkick API Key (plugin desactivado)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "bandsintown_app_id" {
  description = "Bandsintown App ID (plugin desactivado)"
  type        = string
  default     = "metal-travel-tracker"
}

variable "eventbrite_api_key" {
  description = "Eventbrite API Key (plugin desactivado - endpoint descontinuado)"
  type        = string
  sensitive   = true
  default     = ""
}

# ---- Flight APIs ------------------------------------------------------------

variable "amadeus_client_id" {
  description = "Amadeus API Client ID"
  type        = string
  sensitive   = true
}

variable "amadeus_client_secret" {
  description = "Amadeus API Client Secret"
  type        = string
  sensitive   = true
}

variable "serpapi_key" {
  description = "SerpAPI Key for Google Flights (optional)"
  type        = string
  sensitive   = true
  default     = ""
}

# ---- Accommodation APIs -----------------------------------------------------

variable "booking_affiliate_id" {
  description = "Booking.com Affiliate ID"
  type        = string
  sensitive   = true
  default     = ""
}

# ---- Schedule ----------------------------------------------------------------

variable "daily_run_cron" {
  description = "Cron expression for daily execution (UTC). Lima is UTC-5."
  type        = string
  # 13:00 UTC = 08:00 Lima (Lima time)
  default = "cron(0 13 * * ? *)"
}

# ---- Lambda ------------------------------------------------------------------

variable "lambda_timeout_seconds" {
  description = "Timeout for main Lambdas in seconds"
  type        = number
  default     = 900 # 15 minutes (orchestrator needs time for 11 countries + LLM + hotel calls)

  validation {
    condition     = var.lambda_timeout_seconds <= 900
    error_message = "Maximum Lambda timeout is 900 seconds (15 minutes)."
  }
}

variable "lambda_memory_mb" {
  description = "Memory for Lambdas in MB"
  type        = number
  default     = 1024
}

# ---- Bedrock -----------------------------------------------------------------

variable "bedrock_model_id" {
  description = "Bedrock model ID to use (cross-region inference profile recommended)"
  type        = string
  default     = "us.anthropic.claude-sonnet-4-6"
}
