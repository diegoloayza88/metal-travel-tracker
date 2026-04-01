#!/bin/bash
# Script para correr el dashboard localmente
# Requiere: pip install -r src/dashboard/requirements.txt

export DYNAMODB_TABLE_CONCERTS="metal-travel-tracker-prod-concerts"
export AWS_REGION="us-east-1"
export ORCHESTRATOR_FUNCTION_NAME="metal-travel-tracker-prod-orchestrator"

# Usa tu perfil AWS (ajusta si usas uno diferente)
export AWS_PROFILE="${AWS_PROFILE:-default}"

echo "🤘 Iniciando Metal Travel Dashboard..."
echo "   Tabla DynamoDB: $DYNAMODB_TABLE_CONCERTS"
echo "   Región: $AWS_REGION"
echo ""

streamlit run src/dashboard/app.py \
  --server.port 8501 \
  --server.headless false \
  --theme.base dark \
  --theme.primaryColor "#e94560" \
  --theme.backgroundColor "#0e1117" \
  --theme.secondaryBackgroundColor "#1a1a2e"
