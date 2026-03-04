###############################################################################
# terraform/cloudwatch.tf
# Dashboard y alarmas de CloudWatch para monitorear la salud del sistema
###############################################################################

# -----------------------------------------------------------------------------
# Dashboard principal
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.prefix}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      # Fila 1: Invocaciones de Lambdas
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Lambda Invocations"
          period = 86400
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", "${local.prefix}-orchestrator", { label = "Orchestrator" }],
            ["AWS/Lambda", "Invocations", "FunctionName", "${local.prefix}-flight-agent", { label = "Flight Agent" }],
            ["AWS/Lambda", "Invocations", "FunctionName", "${local.prefix}-reporter-agent", { label = "Reporter Agent" }],
            ["AWS/Lambda", "Invocations", "FunctionName", "${local.prefix}-whatsapp-parser", { label = "WhatsApp Parser" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
        }
      },
      # Fila 1: Errores de Lambdas
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title  = "Lambda Errors"
          period = 86400
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", "${local.prefix}-orchestrator", { label = "Orchestrator", color = "#d62728" }],
            ["AWS/Lambda", "Errors", "FunctionName", "${local.prefix}-flight-agent", { label = "Flight Agent", color = "#ff7f0e" }],
            ["AWS/Lambda", "Errors", "FunctionName", "${local.prefix}-whatsapp-parser", { label = "WhatsApp Parser", color = "#9467bd" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
        }
      },
      # Fila 2: Duración de Lambdas
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "Lambda Duration (ms)"
          period = 86400
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", "${local.prefix}-orchestrator", { stat = "p95", label = "Orchestrator P95" }],
            ["AWS/Lambda", "Duration", "FunctionName", "${local.prefix}-flight-agent", { stat = "p95", label = "Flight Agent P95" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
        }
      },
      # Fila 2: Operaciones DynamoDB
      {
        type   = "metric"
        x      = 12
        y      = 6
        width  = 12
        height = 6
        properties = {
          title  = "DynamoDB Operations"
          period = 86400
          metrics = [
            ["AWS/DynamoDB", "SuccessfulRequestLatency", "TableName", "${local.prefix}-concerts", "Operation", "PutItem", { label = "Concerts PutItem" }],
            ["AWS/DynamoDB", "SuccessfulRequestLatency", "TableName", "${local.prefix}-flight-prices", "Operation", "PutItem", { label = "Prices PutItem" }],
          ]
          view   = "timeSeries"
          region = var.aws_region
        }
      },
    ]
  })
}

# -----------------------------------------------------------------------------
# Alarmas de CloudWatch
# -----------------------------------------------------------------------------

# Alarma: Orchestrator falló (no se ejecutó o tuvo error)
resource "aws_cloudwatch_metric_alarm" "orchestrator_errors" {
  alarm_name          = "${local.prefix}-orchestrator-errors"
  alarm_description   = "El Orchestrator Agent tuvo errores en su ejecución diaria"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 86400 # 1 día
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.orchestrator.function_name
  }

  alarm_actions = [aws_sns_topic.cloudwatch_alerts.arn]
  ok_actions    = [aws_sns_topic.cloudwatch_alerts.arn]
}

# Alarma: WhatsApp parser falló al procesar un archivo
resource "aws_cloudwatch_metric_alarm" "whatsapp_parser_errors" {
  alarm_name          = "${local.prefix}-whatsapp-parser-errors"
  alarm_description   = "El WhatsApp Export Parser tuvo errores"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 3600
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.whatsapp_parser.function_name
  }

  alarm_actions = [aws_sns_topic.cloudwatch_alerts.arn]
}

# Alarma: Orchestrator no se ejecutó en 25 horas (se saltó la ejecución diaria)
resource "aws_cloudwatch_metric_alarm" "orchestrator_no_invocations" {
  alarm_name          = "${local.prefix}-orchestrator-no-run"
  alarm_description   = "El Orchestrator no se ejecutó en las últimas 25 horas"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Invocations"
  namespace           = "AWS/Lambda"
  period              = 90000 # 25 horas
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "breaching" # Si no hay datos, ALARM

  dimensions = {
    FunctionName = aws_lambda_function.orchestrator.function_name
  }

  alarm_actions = [aws_sns_topic.cloudwatch_alerts.arn]
}

# -----------------------------------------------------------------------------
# SNS Topic para alertas de CloudWatch (separado del topic de SMS principal)
# -----------------------------------------------------------------------------

resource "aws_sns_topic" "cloudwatch_alerts" {
  name = "${local.prefix}-cloudwatch-alerts"
}

# Suscripción por email para recibir alertas del sistema
resource "aws_sns_topic_subscription" "cloudwatch_email" {
  topic_arn = aws_sns_topic.cloudwatch_alerts.arn
  protocol  = "email"
  endpoint  = var.notification_email_to
}
