# 🤘 Metal Travel Tracker

Sistema multi-agente de IA para detectar conciertos y festivales de metal en países de interés,
y encontrar automáticamente vuelos y alojamientos con buenos precios desde Lima, Perú.

## Arquitectura General

```
EventBridge Scheduler (diario, 8am Lima / UTC-5)
        │
        ▼
Step Functions State Machine
        │
        ▼
Orchestrator Agent (Amazon Bedrock - Claude Sonnet)
├── Concert Agent       → Songkick, Bandsintown, Eventbrite, Metal-Archives, setlist.fm
├── Flight Agent        → Amadeus API, SerpAPI (Google Flights)
├── Hotel Agent         → Booking.com Affiliate API
├── WhatsApp Processor  → Parser de exports .txt (Colombia)
└── Reporter Agent      → Genera reporte en lenguaje natural y notifica

Notificaciones:
├── SMS     → AWS SNS
├── Email   → AWS SES (Gmail)
└── Discord → Webhook
```

## Géneros monitoreados
- Black Metal
- Death Metal
- War Metal
- Heavy Metal
- Thrash Metal

## Países de interés
- 🇨🇴 Colombia
- 🇨🇱 Chile
- 🇧🇷 Brasil
- 🇺🇸 Estados Unidos
- 🇲🇽 México
- 🇫🇮 Finlandia
- 🇪🇸 España

## Origen de vuelos
- Lima, Perú (LIM - Aeropuerto Internacional Jorge Chávez)

---

## Prerequisitos

- AWS CLI configurado con perfil con permisos suficientes
- Terraform >= 1.6
- Python >= 3.13
- Node.js >= 20 (solo si usas el listener de WhatsApp en tiempo real)
- Terraform Cloud cuenta (o S3 backend configurado)

## APIs necesarias (obtener antes de desplegar)

| API | URL | Tier | Costo |
|-----|-----|------|-------|
| Songkick API Key | https://www.songkick.com/api_key_requests/new | 1 | Gratuito |
| Bandsintown App ID | https://artists.bandsintown.com/support/api-installation | 1 | Gratuito |
| Eventbrite API Key | https://www.eventbrite.com/platform/api | 1 | Gratuito |
| Amadeus API (vuelos) | https://developers.amadeus.com | 1 | Gratuito hasta límite |
| SerpAPI (Google Flights) | https://serpapi.com | 2 | $50/mes (opcional) |
| Booking Affiliate | https://join.booking.com/affiliateprogram | 2 | Gratuito con aprobación |
| Discord Webhook | Tu servidor Discord → Configuración de canal | - | Gratuito |

---

## Setup inicial

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/metal-travel-tracker
cd metal-travel-tracker

# 2. Configurar variables de entorno
cp .env.example .env
# Editar .env con tus API keys

# 3. Ejecutar script de setup
chmod +x scripts/setup.sh
./scripts/setup.sh

# 4. Inicializar Terraform
cd terraform
terraform init
terraform plan
terraform apply
```

---

## Subir export de WhatsApp (semanal)

```bash
# Exporta el chat desde tu teléfono como .txt
# Luego súbelo al bucket S3 del proyecto:
aws s3 cp "Exportar chat de Metaleros Colombia.txt" \
  s3://metal-travel-tracker-whatsapp-exports/colombia/$(date +%Y-%m-%d).txt
```

El evento S3 dispara automáticamente el procesador.

---

## Estructura del proyecto

```
metal-travel-tracker/
├── terraform/              # Toda la infraestructura AWS como código
│   ├── modules/            # Módulos reutilizables
│   └── *.tf                # Configuración principal
├── src/
│   ├── agents/             # Los 5 agentes principales
│   ├── plugins/            # Conectores a fuentes de conciertos
│   ├── processors/         # Procesadores especiales (WhatsApp)
│   ├── models/             # Modelos de datos (dataclasses)
│   └── shared/             # Utilidades compartidas
├── .github/workflows/      # CI/CD con GitHub Actions
├── scripts/                # Scripts de utilidad
└── docs/                   # Documentación adicional
```

---

## CI/CD

El pipeline de GitHub Actions hace:
1. `terraform fmt` y `terraform validate` en cada PR
2. `terraform plan` con output en el PR
3. Deploy automático a `main` con `terraform apply`
4. Empaqueta y sube las Lambdas automáticamente

---

## Monitoreo

- **CloudWatch Dashboards**: métricas de cada Lambda y Step Function
- **CloudWatch Alarms**: alertas si algún agente falla
- **LangSmith**: trazabilidad de las llamadas a Bedrock (opcional)

---

## Contribuir

Este es un proyecto personal. Si quieres agregar un nuevo plugin de fuente de conciertos,
implementa la interfaz `ConcertSourcePlugin` en `src/plugins/base.py`.
