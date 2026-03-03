###############################################################################
# terraform/outputs.tf
# Outputs útiles tras el apply
###############################################################################

output "whatsapp_upload_bucket" {
  description = "Bucket S3 donde subir los exports .txt de WhatsApp"
  value       = aws_s3_bucket.whatsapp_exports.bucket
}

output "whatsapp_upload_command" {
  description = "Comando AWS CLI para subir el export de WhatsApp"
  value       = "aws s3 cp 'WhatsApp Chat export.txt' s3://${aws_s3_bucket.whatsapp_exports.bucket}/colombia/$(date +%Y-%m-%d).txt"
}

output "orchestrator_lambda_arn" {
  description = "ARN del Orchestrator Lambda (para invocar manualmente)"
  value       = aws_lambda_function.orchestrator.arn
}

output "manual_trigger_command" {
  description = "Comando para disparar el sistema manualmente"
  value       = "aws lambda invoke --function-name ${aws_lambda_function.orchestrator.function_name} --payload '{\"source\":\"manual\"}' /tmp/response.json && cat /tmp/response.json"
}

output "concerts_table_name" {
  description = "Nombre de la tabla DynamoDB de conciertos"
  value       = aws_dynamodb_table.concerts.name
}

output "cloudwatch_logs_urls" {
  description = "URLs de CloudWatch Logs para cada agente"
  value = {
    orchestrator    = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home#logsV2:log-groups/log-group/${urlencode(aws_cloudwatch_log_group.orchestrator.name)}"
    flight_agent    = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home#logsV2:log-groups/log-group/${urlencode(aws_cloudwatch_log_group.flight_agent.name)}"
    reporter_agent  = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home#logsV2:log-groups/log-group/${urlencode(aws_cloudwatch_log_group.reporter_agent.name)}"
    whatsapp_parser = "https://${var.aws_region}.console.aws.amazon.com/cloudwatch/home#logsV2:log-groups/log-group/${urlencode(aws_cloudwatch_log_group.whatsapp_parser.name)}"
  }
}

output "ses_verification_note" {
  description = "Recordatorio sobre verificación de SES"
  value       = "IMPORTANTE: Revisa tu email ${var.notification_email_from} para verificar la identidad en SES antes de que los emails funcionen."
}
