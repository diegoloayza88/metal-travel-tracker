###############################################################################
# terraform/cloudwatch.tf
# CloudWatch dashboard and alarms to monitor system health
###############################################################################

# -----------------------------------------------------------------------------
# Main dashboard
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.prefix}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      # Row 1: Lambda invocations
      {
        type   = "metric"
        x      = 0; y = 0; width = 12; height = 6
        properties = {
          title  = "Lambda Invocations"
          period = 86400  # Daily
          metrics = [
            ["AWS/Lambda", "Invocations", "FunctionName", "${local.prefix}-orchestrator",  {label = "Orchestrator"}],
            ["AWS/Lambda", "Invocations", "FunctionName", "${local.prefix}-flight-agent",   {label = "Flight Agent"}],
            ["AWS/Lambda", "Invocations", "FunctionName", "${local.prefix}-reporter-agent", {label = "Reporter Agent"}],
            ["AWS/Lambda", "Invocations", "FunctionName", "${local.prefix}-whatsapp-parser",{label = "WhatsApp Parser"}],
          ]
          view   = "timeSeries"
          region = var.aws_region
        }
      },
      # Row 1: Lambda errors
      {
        type   = "metric"
        x      = 12; y = 0; width = 12; height = 6
        properties = {
          title  = "Lambda Errors"
          period = 86400
          metrics = [
            ["AWS/Lambda", "Errors", "FunctionName", "${local.prefix}-orchestrator",   {label = "Orchestrator",   color = "#d62728"}],
            ["AWS/Lambda", "Errors", "FunctionName", "${local.prefix}-flight-agent",   {label = "Flight Agent",   color = "#ff7f0e"}],
            ["AWS/Lambda", "Errors", "FunctionName", "${local.prefix}-whatsapp-parser",{label = "WhatsApp Parser",color = "#9467bd"}],
          ]
          view   = "timeSeries"
          region = var.aws_region
        }
      },
      # Row 2: Lambda duration
      {
        type   = "metric"
        x      = 0; y = 6; width = 12; height = 6
        properties = {
          title  = "Lambda Duration (ms)"
          period = 86400
          metrics = [
            ["AWS/Lambda", "Duration", "FunctionName", "${local.prefix}-orchestrator",  {stat = "p95", label = "Orchestrator P95"}],
            ["AWS/Lambda", "Duration", "FunctionName", "${local.prefix}-flight-agent",  {stat = "p95", label = "Flight Agent P95"}],
          ]
          view   = "timeSeries"
          region = var.aws_region
        }
      },
      # Row 2: DynamoDB items
      {
        type   = "metric"
        x      = 12; y = 6; width = 12; height = 6
        properties = {
          title  = "DynamoDB Operations"
          period = 86400
          metrics = [
            ["AWS/DynamoDB", "SuccessfulRequestLatency", "TableName", "${local.prefix}-concerts",      "Operation", "PutItem", {label = "Concerts PutItem"}],
            ["AWS/DynamoDB", "SuccessfulRequestLatency", "TableName", "${local.prefix}-flight-prices", "Operation", "PutItem", {label = "Prices PutItem"}],
          ]
          view   = "timeSeries"
          region = var.aws_region
        }
      },
    ]
  })
}

# -----------------------------------------------------------------------------
# CloudWatch Alarms
# -----------------------------------------------------------------------------

# Alarm: Orchestrator failed (did not run or had an error)
resource "aws_cloudwatch_metric_alarm" "orchestrator_errors" {
  alarm_name          = "${local.prefix}-orchestrator-errors"
  alarm_description   = "The Orchestrator Agent had errors during its daily execution"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 86400  # 1 day
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.orchestrator.function_name
  }

  alarm_actions = [aws_sns_topic.cloudwatch_alerts.arn]
  ok_actions    = [aws_sns_topic.cloudwatch_alerts.arn]
}

# Alarm: WhatsApp parser failed to process a file
resource "aws_cloudwatch_metric_alarm" "whatsapp_parser_errors" {
  alarm_name          = "${local.prefix}-whatsapp-parser-errors"
  alarm_description   = "The WhatsApp Export Parser had errors"
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

# Alarm: Orchestrator did not run in 25 hours (missed the daily execution)
resource "aws_cloudwatch_metric_alarm" "orchestrator_no_invocations" {
  alarm_name          = "${local.prefix}-orchestrator-no-run"
  alarm_description   = "The Orchestrator did not run in the last 25 hours"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Invocations"
  namespace           = "AWS/Lambda"
  period              = 90000  # 25 hours
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "breaching"  # If no data, ALARM

  dimensions = {
    FunctionName = aws_lambda_function.orchestrator.function_name
  }

  alarm_actions = [aws_sns_topic.cloudwatch_alerts.arn]
}

# -----------------------------------------------------------------------------
# SNS Topic for CloudWatch alerts (separate from the main SMS topic)
# -----------------------------------------------------------------------------

resource "aws_sns_topic" "cloudwatch_alerts" {
  name = "${local.prefix}-cloudwatch-alerts"
}

# Email subscription to receive system alerts
resource "aws_sns_topic_subscription" "cloudwatch_email" {
  topic_arn = aws_sns_topic.cloudwatch_alerts.arn
  protocol  = "email"
  endpoint  = var.notification_email_to
}