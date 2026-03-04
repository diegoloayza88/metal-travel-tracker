###############################################################################
# terraform/main.tf
# Main resources: DynamoDB, S3, Secrets Manager
###############################################################################

locals {
  prefix = "${var.project_name}-${var.environment}"

  # Common environment variables for all Lambdas
  common_env_vars = {
    ENVIRONMENT                  = var.environment
    AWS_REGION_NAME              = var.aws_region
    DYNAMODB_TABLE_CONCERTS      = aws_dynamodb_table.concerts.name
    DYNAMODB_TABLE_FLIGHTS       = aws_dynamodb_table.flight_prices.name
    BEDROCK_MODEL_ID             = var.bedrock_model_id
    DISCORD_WEBHOOK_URL          = var.discord_webhook_url
    SNS_PHONE_NUMBER             = var.notification_phone_number
    SES_FROM_EMAIL               = var.notification_email_from
    SES_TO_EMAIL                 = var.notification_email_to
    FLIGHT_AGENT_FUNCTION_NAME   = "${local.prefix}-flight-agent"
    REPORTER_AGENT_FUNCTION_NAME = "${local.prefix}-reporter-agent"
    HOTEL_AGENT_FUNCTION_NAME    = "${local.prefix}-hotel-agent"
  }
}

# -----------------------------------------------------------------------------
# DynamoDB Tables
# -----------------------------------------------------------------------------

# Main concerts table
resource "aws_dynamodb_table" "concerts" {
  name         = "${local.prefix}-concerts"
  billing_mode = "PAY_PER_REQUEST" # On-demand, no fixed capacity to pay for
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  # TTL: concerts expire 30 days after the event
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  # Index to look up concerts by source
  global_secondary_index {
    name            = "source-index"
    hash_key        = "source"
    projection_type = "ALL"
  }

  attribute {
    name = "source"
    type = "S"
  }

  tags = {
    Name = "${local.prefix}-concerts"
  }
}

# Historical flight prices table (for deal analysis)
resource "aws_dynamodb_table" "flight_prices" {
  name         = "${local.prefix}-flight-prices"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  # TTL: prices expire after 90 days
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Name = "${local.prefix}-flight-prices"
  }
}

# Notified deals table (to avoid re-notifying the same deal)
resource "aws_dynamodb_table" "notified_deals" {
  name         = "${local.prefix}-notified-deals"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Name = "${local.prefix}-notified-deals"
  }
}

# -----------------------------------------------------------------------------
# S3 Buckets
# -----------------------------------------------------------------------------

# Bucket for WhatsApp export uploads
resource "aws_s3_bucket" "whatsapp_exports" {
  bucket = "${local.prefix}-whatsapp-exports"

  tags = {
    Name    = "${local.prefix}-whatsapp-exports"
    Purpose = "WhatsApp chat exports for concert parsing"
  }
}

resource "aws_s3_bucket_versioning" "whatsapp_exports" {
  bucket = aws_s3_bucket.whatsapp_exports.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Block public access to the bucket (it is private)
resource "aws_s3_bucket_public_access_block" "whatsapp_exports" {
  bucket                  = aws_s3_bucket.whatsapp_exports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 notification → Lambda when a .txt is uploaded
resource "aws_s3_bucket_notification" "whatsapp_export_trigger" {
  bucket = aws_s3_bucket.whatsapp_exports.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.whatsapp_parser.arn
    events              = ["s3:ObjectCreated:*"]
    filter_suffix       = ".txt"
  }

  depends_on = [aws_lambda_permission.s3_invoke_whatsapp_parser]
}

# Bucket for Lambda code (deployment packages)
resource "aws_s3_bucket" "lambda_code" {
  bucket = "${local.prefix}-lambda-code"

  tags = {
    Name    = "${local.prefix}-lambda-code"
    Purpose = "Lambda deployment packages"
  }
}

resource "aws_s3_bucket_public_access_block" "lambda_code" {
  bucket                  = aws_s3_bucket.lambda_code.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -----------------------------------------------------------------------------
# Secrets Manager (API Keys)
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "api_keys" {
  name                    = "${local.prefix}/api-keys"
  description             = "API keys for all external sources of Metal Travel Tracker"
  recovery_window_in_days = 7

  tags = {
    Name = "${local.prefix}-api-keys"
  }
}

resource "aws_secretsmanager_secret_version" "api_keys" {
  secret_id = aws_secretsmanager_secret.api_keys.id
  secret_string = jsonencode({
    SONGKICK_API_KEY      = var.songkick_api_key
    BANDSINTOWN_APP_ID    = var.bandsintown_app_id
    EVENTBRITE_API_KEY    = var.eventbrite_api_key
    AMADEUS_CLIENT_ID     = var.amadeus_client_id
    AMADEUS_CLIENT_SECRET = var.amadeus_client_secret
    SERPAPI_KEY           = var.serpapi_key
    BOOKING_AFFILIATE_ID  = var.booking_affiliate_id
  })
}

# -----------------------------------------------------------------------------
# SNS Topic for SMS
# -----------------------------------------------------------------------------

resource "aws_sns_topic" "alerts" {
  name = "${local.prefix}-alerts"

  tags = {
    Name = "${local.prefix}-alerts"
  }
}

# -----------------------------------------------------------------------------
# SES Email Identity
# -----------------------------------------------------------------------------

resource "aws_ses_email_identity" "from" {
  email = var.notification_email_from
}

# Note: After applying, AWS will send a verification email to this address.
# You must click the link before SES can send emails.
