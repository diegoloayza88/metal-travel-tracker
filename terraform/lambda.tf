###############################################################################
# terraform/lambda.tf
# Definition of all project Lambda functions
###############################################################################

# -----------------------------------------------------------------------------
# Python code packaging
# Each Lambda has its own zip with its dependencies
# -----------------------------------------------------------------------------
locals {
  build_dir   = "${path.root}/../.build"
  code_bucket = aws_s3_bucket.lambda_code.bucket
}

# -----------------------------------------------------------------------------
# Lambda: Orchestrator Agent
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "orchestrator" {
  function_name = "${local.prefix}-orchestrator"
  description   = "Orchestrator Agent - coordina todos los demás agentes diariamente"
  role          = aws_iam_role.lambda_execution.arn
  handler       = "agents.orchestrator.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  s3_bucket = local.code_bucket
  s3_key    = "orchestrator.zip"

  environment {
    variables = merge(local.common_env_vars, {
      SECRETS_ARN = aws_secretsmanager_secret.api_keys.arn
    })
  }

  layers     = [aws_lambda_layer_version.python_deps.arn]
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
  description   = "Flight Agent - busca vuelos desde Lima y analiza precios"
  role          = aws_iam_role.lambda_execution.arn
  handler       = "agents.flight_agent.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  s3_bucket = local.code_bucket
  s3_key    = "flight_agent.zip"

  environment {
    variables = merge(local.common_env_vars, {
      SECRETS_ARN = aws_secretsmanager_secret.api_keys.arn
    })
  }

  layers     = [aws_lambda_layer_version.python_deps.arn]
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
  description   = "Hotel Agent - busca alojamiento cerca del venue del concierto"
  role          = aws_iam_role.lambda_execution.arn
  handler       = "agents.hotel_agent.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  s3_bucket = local.code_bucket
  s3_key    = "hotel_agent.zip"

  environment {
    variables = merge(local.common_env_vars, {
      SECRETS_ARN = aws_secretsmanager_secret.api_keys.arn
    })
  }

  layers     = [aws_lambda_layer_version.python_deps.arn]
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
  description   = "Reporter Agent - genera reportes con LLM y envía notificaciones"
  role          = aws_iam_role.lambda_execution.arn
  handler       = "agents.reporter_agent.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  s3_bucket = local.code_bucket
  s3_key    = "reporter_agent.zip"

  environment {
    variables = local.common_env_vars
  }

  layers     = [aws_lambda_layer_version.python_deps.arn]
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
  description   = "Procesa exports .txt de WhatsApp y extrae conciertos con LLM"
  role          = aws_iam_role.lambda_execution.arn
  handler       = "processors.whatsapp_export_parser.handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = var.lambda_timeout_seconds
  memory_size   = var.lambda_memory_mb

  s3_bucket = local.code_bucket
  s3_key    = "whatsapp_parser.zip"

  environment {
    variables = local.common_env_vars
  }

  layers     = [aws_lambda_layer_version.python_deps.arn]
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
  description         = "Dependencias Python: httpx, etc."
  compatible_runtimes = ["python3.13"]

  s3_bucket = local.code_bucket
  s3_key    = "layer.zip"
}