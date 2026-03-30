###############################################################################
# terraform/iam.tf
# IAM Roles and policies for Lambdas
# Principle of least privilege: each Lambda only has access to what it needs
###############################################################################

# -----------------------------------------------------------------------------
# Main IAM execution Role for Lambdas
# -----------------------------------------------------------------------------

resource "aws_iam_role" "lambda_execution" {
  name        = "${local.prefix}-lambda-execution-role"
  description = "Execution role for all Metal Travel Tracker Lambdas"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${local.prefix}-lambda-execution-role"
  }
}

# -----------------------------------------------------------------------------
# IAM Policies
# -----------------------------------------------------------------------------

# Policy: CloudWatch Logs (basic for all Lambdas)
resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Policy: DynamoDB (only project tables)
resource "aws_iam_policy" "dynamodb_access" {
  name        = "${local.prefix}-dynamodb-access"
  description = "Access to project DynamoDB tables"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:BatchWriteItem",
          "dynamodb:BatchGetItem",
        ]
        Resource = [
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${local.prefix}-*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "dynamodb_access" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = aws_iam_policy.dynamodb_access.arn
}

# Policy: S3 (only project buckets)
resource "aws_iam_policy" "s3_access" {
  name        = "${local.prefix}-s3-access"
  description = "Access to project S3 buckets"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.whatsapp_exports.arn,
          "${aws_s3_bucket.whatsapp_exports.arn}/*",
          aws_s3_bucket.lambda_code.arn,
          "${aws_s3_bucket.lambda_code.arn}/*",
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "s3_access" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = aws_iam_policy.s3_access.arn
}

# Policy: Secrets Manager (read API keys)
resource "aws_iam_policy" "secrets_access" {
  name        = "${local.prefix}-secrets-access"
  description = "Read API keys from Secrets Manager"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret",
      ]
      Resource = aws_secretsmanager_secret.api_keys.arn
    }]
  })
}

resource "aws_iam_role_policy_attachment" "secrets_access" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = aws_iam_policy.secrets_access.arn
}

# Policy: Amazon Bedrock (invoke Claude models)
resource "aws_iam_policy" "bedrock_access" {
  name        = "${local.prefix}-bedrock-access"
  description = "Invoke Amazon Bedrock models"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
      ]
      Resource = [
        # Cross-region inference profiles (required for us.anthropic.* model IDs)
        "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/us.anthropic.claude-sonnet-4-6",
        "arn:aws:bedrock:${var.aws_region}:${data.aws_caller_identity.current.account_id}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
        # Underlying foundation models (needed for cross-region routing)
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
      ]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "bedrock_access" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = aws_iam_policy.bedrock_access.arn
}

# Policy: SNS (send SMS)
resource "aws_iam_policy" "sns_access" {
  name        = "${local.prefix}-sns-access"
  description = "Publish SNS messages for SMS"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sns:Publish"]
      Resource = "*" # Direct SNS SMS does not have a specific topic ARN
    }]
  })
}

resource "aws_iam_role_policy_attachment" "sns_access" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = aws_iam_policy.sns_access.arn
}

# Policy: SES (send emails)
resource "aws_iam_policy" "ses_access" {
  name        = "${local.prefix}-ses-access"
  description = "Send emails via SES"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ses:SendEmail", "ses:SendRawEmail"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ses_access" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = aws_iam_policy.ses_access.arn
}

# Policy: Lambda Invoke (for the Orchestrator to call other agents)
resource "aws_iam_policy" "lambda_invoke" {
  name        = "${local.prefix}-lambda-invoke"
  description = "Allows the Orchestrator to invoke other Lambda agents"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["lambda:InvokeFunction"]
      Resource = [
        aws_lambda_function.flight_agent.arn,
        aws_lambda_function.hotel_agent.arn,
        aws_lambda_function.reporter_agent.arn,
      ]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_invoke" {
  role       = aws_iam_role.lambda_execution.name
  policy_arn = aws_iam_policy.lambda_invoke.arn
}