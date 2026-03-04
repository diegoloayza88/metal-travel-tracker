#!/usr/bin/env bash
set -euo pipefail

echo "🤘 Metal Travel Tracker — Setup Inicial"
echo "========================================"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# -----------------------------------------------------------------------------
# 1. Verificar dependencias
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[1/4] Verificando dependencias...${NC}"

check_command() {
  if ! command -v "$1" &> /dev/null; then
    echo -e "${RED}ERROR: $1 no está instalado${NC}"
    exit 1
  fi
  echo -e "  ✅ $1: $(command -v $1)"
}

check_command aws
check_command terraform
check_command python3
check_command pip

echo -e "${GREEN}Todas las dependencias encontradas${NC}"

# -----------------------------------------------------------------------------
# 2. Verificar login en Terraform Cloud
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[2/4] Verificando Terraform Cloud...${NC}"

if ! terraform whoami &> /dev/null; then
  echo -e "  ${YELLOW}No estás logueado en Terraform Cloud.${NC}"
  echo -e "  Ejecuta: ${YELLOW}terraform login${NC}"
  echo -e "  Luego vuelve a correr este script."
  exit 1
fi

TFC_USER=$(terraform whoami)
echo -e "  ✅ Logueado en Terraform Cloud como: $TFC_USER"
echo -e "  ${YELLOW}⚠️  Asegúrate de que el workspace 'metal-travel-tracker' existe en tu organización${NC}"
echo -e "  ${YELLOW}    y de haber reemplazado 'tu-organizacion-en-tfc' en terraform/providers.tf${NC}"

# -----------------------------------------------------------------------------
# 3. Crear terraform.tfvars
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[3/4] Creando terraform.tfvars...${NC}"

TFVARS_FILE="terraform/terraform.tfvars"
if [ -f "$TFVARS_FILE" ]; then
  echo -e "  ⚠️  $TFVARS_FILE ya existe, no se sobreescribirá"
else
  cat > "$TFVARS_FILE" << 'EOF'
###############################################################################
# terraform.tfvars — NO commitear (está en .gitignore)
# Nota: con Terraform Cloud, las variables sensibles (API keys, AWS credentials)
# se configuran en el workspace de TFC, no aquí.
# Este archivo es solo para desarrollo local si corres terraform localmente.
###############################################################################

aws_region  = "us-east-1"
environment = "prod"

notification_phone_number = "+51"
notification_email_from   = ""
notification_email_to     = ""
discord_webhook_url       = ""

songkick_api_key       = ""
bandsintown_app_id     = "metal-travel-tracker"
eventbrite_api_key     = ""
amadeus_client_id      = ""
amadeus_client_secret  = ""
serpapi_key            = ""
booking_affiliate_id   = ""
EOF
  echo -e "  ✅ Archivo creado: $TFVARS_FILE"
fi

# -----------------------------------------------------------------------------
# 4. Instalar dependencias Python
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[4/4] Instalando dependencias Python...${NC}"
pip install -r requirements.txt -q
mkdir -p .build
echo -e "  ✅ Dependencias instaladas"

# -----------------------------------------------------------------------------
# Resumen
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}========================================"
echo -e "✅ Setup completado"
echo -e "========================================${NC}"
echo -e "\nPróximos pasos:"
echo -e "  1. Edita ${YELLOW}terraform/providers.tf${NC} con tu organización de TFC"
echo -e "  2. Configura las variables en el workspace de TFC (ver README)"
echo -e "  3. Ejecuta: ${YELLOW}cd terraform && terraform init${NC}"
echo -e "  4. Ejecuta: ${YELLOW}terraform plan${NC}"
echo -e "  5. Si el plan se ve bien: ${YELLOW}terraform apply${NC}"