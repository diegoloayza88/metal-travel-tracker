terraform {
  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "2.7.1"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "6.17.0"
    }
  }
}
###############################################################################
# terraform/lambda.tf
# Definition of all project Lambda functions
###############################################################################

# -----------------------------------------------------------------------------
# Python code packaging
# Each Lambda has its own zip with its dependencies
# -----------------------------------------------------------------------------

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.root}/../src"
  output_path = "${path.root}/../.build/orchestrator.zip"
}

data "archive_file" "flight_agent" {
  type        = "zip"
  source_dir  = "${path.root}/../src"
  output_path = "${path.root}/../.build/flight_agent.zip"
}

data "archive_file" "hotel_agent" {
  type        = "zip"
  source_dir  = "${path.root}/../src"
  output_path = "${path.root}/../.build/hotel_agent.zip"
}

data "archive_file" "reporter_agent" {
  type        = "zip"
  source_dir  = "${path.root}/../src"
  output_path = "${path.root}/../.build/reporter_agent.zip"
}

data "archive_file" "whatsapp_parser" {
  type        = "zip"
  source_dir  = "${path.root}/../src"
  output_path = "${path.root}/../.build/whatsapp_parser.zip"
}

# -----------------------------------------------------------------------------
# Lambda: Orchestrator Agent
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "orchestrator" {
  function_name = "${local.prefix}-orchestrator"
  description   = "Orchestrator Agent - coordinates the rest of the agents on a daily basis."
  role          = aws_iam_role.lambda_execution.arn
  handler       = "agents.orchestrator.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  filename         = data.archive_file.orchestrator.output_path
  source_code_hash = data.archive_file.orchestrator.output_base64sha256

  environment {
    variables = merge(local.common_env_vars, {
      SECRETS_ARN = aws_secretsmanager_secret.api_keys.arn
    })
  }

  layers = [aws_lambda_layer_version.python_deps.arn]

  # Cloudwatch retention logs for 30 days
  depends_on = [aws_cloudwatch_log_group.orchestrator]

  tags = {
    Name  = "${local.prefix}-orchestrator"
    Agent = "orchestrator"
  }
}

resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/aws/lambda/${local.prefix}-orchestrator"
  retention_in_days = 30
}

# -----------------------------------------------------------------------------
# Lambda: Flight Agent
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "flight_agent" {
  function_name = "${local.prefix}-flight-agent"
  description   = "Flight Agent - Looks for flights from Lima and analyses prices."
  role          = aws_iam_role.lambda_execution.arn
  handler       = "agents.flight_agent.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  filename         = data.archive_file.flight_agent.output_path
  source_code_hash = data.archive_file.flight_agent.output_base64sha256

  environment {
    variables = merge(local.common_env_vars, {
      SECRETS_ARN = aws_secretsmanager_secret.api_keys.arn
    })
  }

  layers = [aws_lambda_layer_version.python_deps.arn]

  depends_on = [aws_cloudwatch_log_group.flight_agent]

  tags = {
    Name  = "${local.prefix}-flight-agent"
    Agent = "flight"
  }
}

resource "aws_cloudwatch_log_group" "flight_agent" {
  name              = "/aws/lambda/${local.prefix}-flight-agent"
  retention_in_days = 30
}

# -----------------------------------------------------------------------------
# Lambda: Hotel Agent
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "hotel_agent" {
  function_name = "${local.prefix}-hotel-agent"
  description   = "Hotel Agent - Find best accommodations close to the concert venues."
  role          = aws_iam_role.lambda_execution.arn
  handler       = "agents.hotel_agent.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  filename         = data.archive_file.hotel_agent.output_path
  source_code_hash = data.archive_file.hotel_agent.output_base64sha256

  environment {
    variables = merge(local.common_env_vars, {
      SECRETS_ARN = aws_secretsmanager_secret.api_keys.arn
    })
  }

  layers = [aws_lambda_layer_version.python_deps.arn]

  depends_on = [aws_cloudwatch_log_group.hotel_agent]

  tags = {
    Name  = "${local.prefix}-hotel-agent"
    Agent = "hotel"
  }
}

resource "aws_cloudwatch_log_group" "hotel_agent" {
  name              = "/aws/lambda/${local.prefix}-hotel-agent"
  retention_in_days = 30
}

# -----------------------------------------------------------------------------
# Lambda: Reporter Agent
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "reporter_agent" {
  function_name = "${local.prefix}-reporter-agent"
  description   = "Reporter Agent - Generates reports with LLM and send notifications."
  role          = aws_iam_role.lambda_execution.arn
  handler       = "agents.reporter_agent.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  filename         = data.archive_file.reporter_agent.output_path
  source_code_hash = data.archive_file.reporter_agent.output_base64sha256

  environment {
    variables = local.common_env_vars
  }

  layers = [aws_lambda_layer_version.python_deps.arn]

  depends_on = [aws_cloudwatch_log_group.reporter_agent]

  tags = {
    Name  = "${local.prefix}-reporter-agent"
    Agent = "reporter"
  }
}

resource "aws_cloudwatch_log_group" "reporter_agent" {
  name              = "/aws/lambda/${local.prefix}-reporter-agent"
  retention_in_days = 30
}

# -----------------------------------------------------------------------------
# Lambda: WhatsApp Export Parser
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "whatsapp_parser" {
  function_name = "${local.prefix}-whatsapp-parser"
  description   = "Processes WhatsApp .txt exports and extracts concerts using LLM"
  role          = aws_iam_role.lambda_execution.arn
  handler       = "processors.whatsapp_export_parser.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = 300  # Files can be large
  memory_size   = 512

  filename         = data.archive_file.whatsapp_parser.output_path
  source_code_hash = data.archive_file.whatsapp_parser.output_base64sha256

  environment {
    variables = local.common_env_vars
  }

  layers = [aws_lambda_layer_version.python_deps.arn]

  depends_on = [aws_cloudwatch_log_group.whatsapp_parser]

  tags = {
    Name    = "${local.prefix}-whatsapp-parser"
    Trigger = "s3-event"
  }
}

resource "aws_cloudwatch_log_group" "whatsapp_parser" {
  name              = "/aws/lambda/${local.prefix}-whatsapp-parser"
  retention_in_days = 30
}

resource "aws_lambda_permission" "s3_invoke_whatsapp_parser" {
  statement_id  = "AllowS3Invocation"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.whatsapp_parser.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.whatsapp_exports.arn
}

# -----------------------------------------------------------------------------
# Lambda Layer: Shared Python dependencies
# Dependencies (httpx, boto3 extras, etc.) go in a Layer to avoid
# repeating them in each Lambda and keep the zips small.
# -----------------------------------------------------------------------------

resource "aws_lambda_layer_version" "python_deps" {
  layer_name          = "${local.prefix}-python-deps"
  description         = "Python dependencies: httpx, etc."
  compatible_runtimes = ["python3.13"]

  # The layer is built in CI/CD with: pip install -r requirements.txt -t layer/python/
  filename         = "${path.root}/../.build/layer.zip"
  source_code_hash = filebase64sha256("${path.root}/../.build/layer.zip")
}