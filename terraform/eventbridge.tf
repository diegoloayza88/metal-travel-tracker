###############################################################################
# terraform/eventbridge.tf
# EventBridge Scheduler for the daily Orchestrator execution
###############################################################################

# -----------------------------------------------------------------------------
# Daily Scheduler → Orchestrator Lambda
# -----------------------------------------------------------------------------

resource "aws_scheduler_schedule" "daily_run" {
  name        = "${local.prefix}-daily-run"
  description = "Runs Metal Travel Tracker daily at 8am Lima (UTC-5)"
  group_name  = "default"

  # 13:00 UTC = 08:00 Lima (America/Lima is UTC-5)
  schedule_expression          = var.daily_run_cron
  schedule_expression_timezone = "America/Lima"

  flexible_time_window {
    mode                      = "FLEXIBLE"
    maximum_window_in_minutes = 15 # Can execute up to 15min after 8am
  }

  target {
    arn      = aws_lambda_function.orchestrator.arn
    role_arn = aws_iam_role.scheduler_execution.arn

    input = jsonencode({
      source       = "eventbridge-scheduler"
      run_type     = "daily"
      triggered_at = "auto"
    })

    retry_policy {
      maximum_retry_attempts       = 3
      maximum_event_age_in_seconds = 3600
    }
  }
}

# -----------------------------------------------------------------------------
# Weekly Scheduler → Reporter Agent (full Sunday report)
# -----------------------------------------------------------------------------

resource "aws_scheduler_schedule" "weekly_report" {
  name        = "${local.prefix}-weekly-report"
  description = "Full weekly report, every Sunday at 9am Lima"
  group_name  = "default"

  schedule_expression          = "cron(0 14 ? * SUN *)" # 14:00 UTC = 09:00 Lima Sunday
  schedule_expression_timezone = "America/Lima"

  flexible_time_window {
    mode = "OFF" # The weekly report must be on time
  }

  target {
    arn      = aws_lambda_function.reporter_agent.arn
    role_arn = aws_iam_role.scheduler_execution.arn

    input = jsonencode({
      source           = "eventbridge-scheduler"
      is_weekly_report = true
      report_date      = "auto"
    })

    retry_policy {
      maximum_retry_attempts       = 2
      maximum_event_age_in_seconds = 1800
    }
  }
}

# -----------------------------------------------------------------------------
# IAM Role for EventBridge Scheduler
# -----------------------------------------------------------------------------

resource "aws_iam_role" "scheduler_execution" {
  name        = "${local.prefix}-scheduler-execution-role"
  description = "Role for EventBridge Scheduler to invoke Lambdas"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

resource "aws_iam_policy" "scheduler_invoke_lambda" {
  name        = "${local.prefix}-scheduler-invoke-lambda"
  description = "Allows the Scheduler to invoke project Lambdas"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["lambda:InvokeFunction"]
      Resource = [
        aws_lambda_function.orchestrator.arn,
        aws_lambda_function.reporter_agent.arn,
      ]
    }]
  })
}

resource "aws_iam_role_policy_attachment" "scheduler_invoke_lambda" {
  role       = aws_iam_role.scheduler_execution.name
  policy_arn = aws_iam_policy.scheduler_invoke_lambda.arn
}

# Permission for EventBridge to invoke the Orchestrator Lambda
resource "aws_lambda_permission" "allow_eventbridge_orchestrator" {
  statement_id  = "AllowEventBridgeInvocation"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orchestrator.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.daily_run.arn
}

resource "aws_lambda_permission" "allow_eventbridge_reporter" {
  statement_id  = "AllowEventBridgeWeeklyReport"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.reporter_agent.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = aws_scheduler_schedule.weekly_report.arn
}

# Data source to get the Account ID
data "aws_caller_identity" "current" {}