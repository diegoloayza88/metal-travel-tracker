"""
processors/whatsapp_export_parser/handler.py
--------------------------------------------
Procesador del archivo .txt exportado del grupo/comunidad de WhatsApp.

Flujo:
  1. Se sube el .txt exportado de WhatsApp al bucket S3 (manualmente, 1 vez/semana)
  2. S3 dispara este Lambda automáticamente via Event Notification
  3. El Lambda lee el archivo, extrae mensajes individuales
  4. Por cada mensaje, llama a Bedrock (Claude) para extraer info estructurada
  5. Los conciertos válidos se guardan en DynamoDB
  6. Se notifica vía Discord cuántos conciertos nuevos se encontraron

Formato del .txt exportado por WhatsApp (Android/iOS):
  "12/2/26, 10:23 a. m. - Juan: CONFIRMADO Sepultura en Bogotá 15 marzo"
  "12/2/26, 10:23 a. m. - Juan: Teatro Royal. Entradas desde $80.000"
  (los mensajes largos pueden ocupar múltiples líneas)
"""

import json
import logging
import os
import re

import boto3

from src.models.concert import Concert, Country, SourceTier
from src.shared.bedrock_client import BedrockClient
from src.shared.dynamodb_client import DynamoDBClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Regex para detectar el inicio de un mensaje de WhatsApp
# Soporta formatos de fecha Android (DD/MM/YY) e iOS (DD-MM-YYYY)
WHATSAPP_MESSAGE_PATTERN = re.compile(
    r"^(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}),?\s+\d{1,2}:\d{2}(?:\s?[ap]\.\s?m\.)?\s+-\s+(.+?):\s+(.+)$",
    re.MULTILINE,
)

# País por defecto para este grupo (Colombia)
DEFAULT_COUNTRY = Country.COLOMBIA


def lambda_handler(event: dict, context) -> dict:
    """
    Entry point del Lambda.
    Recibe el evento de S3 cuando se sube un nuevo archivo .txt.
    """
    logger.info(f"Evento recibido: {json.dumps(event)}")

    s3_client = boto3.client("s3")
    bedrock = BedrockClient()
    dynamodb = DynamoDBClient(table_name=os.environ["DYNAMODB_TABLE_CONCERTS"])

    processed_count = 0
    new_concerts = 0

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        logger.info(f"Procesando archivo: s3://{bucket}/{key}")

        # 1. Descargar el archivo .txt desde S3
        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            raw_text = response["Body"].read().decode("utf-8", errors="replace")
        except Exception as e:
            logger.error(f"Error al descargar {key}: {e}")
            continue

        # 2. Extraer mensajes individuales del texto
        messages = extract_messages(raw_text)
        logger.info(f"Mensajes encontrados en el archivo: {len(messages)}")

        # 3. Procesar cada mensaje con Bedrock
        for message_text in messages:
            processed_count += 1

            # Saltar mensajes muy cortos (probablemente no son anuncios)
            if len(message_text.strip()) < 20:
                continue

            # Saltar mensajes de sistema de WhatsApp
            if _is_system_message(message_text):
                continue

            concert = process_message_with_llm(message_text, bedrock)

            if concert:
                # Verificar si ya existe en DynamoDB para evitar duplicados
                if not dynamodb.exists(concert.unique_key):
                    dynamodb.save_concert(concert)
                    new_concerts += 1
                    logger.info(
                        f"Nuevo concierto guardado: {concert.band_name} en {concert.city} el {concert.event_date_str}"
                    )

    result = {
        "statusCode": 200,
        "processed_messages": processed_count,
        "new_concerts": new_concerts,
        "source": "whatsapp_colombia",
    }

    logger.info(f"Procesamiento completado: {result}")

    # 4. Notificar por Discord si se encontraron conciertos nuevos
    if new_concerts > 0:
        _notify_discord(new_concerts, bucket, key)

    return result


# ---------------------------------------------------------------------------
# Extracción de mensajes del .txt de WhatsApp
# ---------------------------------------------------------------------------


def extract_messages(raw_text: str) -> list[str]:
    """
    Extrae el contenido de cada mensaje del archivo .txt exportado.
    Maneja mensajes multilínea correctamente.

    Args:
        raw_text: Contenido completo del archivo exportado.

    Returns:
        Lista de textos de mensajes (solo el contenido, sin fecha/autor).
    """
    messages = []
    current_message_lines = []

    for line in raw_text.splitlines():
        if WHATSAPP_MESSAGE_PATTERN.match(line):
            # Nueva línea que es inicio de mensaje
            # Guardar el mensaje anterior si existe
            if current_message_lines:
                messages.append(" ".join(current_message_lines))

            # Extraer solo el contenido del mensaje (sin fecha y autor)
            match = WHATSAPP_MESSAGE_PATTERN.match(line)
            if match:
                message_content = match.group(3)
                current_message_lines = [message_content]
        else:
            # Continuación del mensaje anterior (mensaje multilínea)
            if current_message_lines:
                current_message_lines.append(line)

    # No olvidar el último mensaje
    if current_message_lines:
        messages.append(" ".join(current_message_lines))

    return messages


# ---------------------------------------------------------------------------
# Procesamiento con LLM (Bedrock)
# ---------------------------------------------------------------------------


def process_message_with_llm(
    message_text: str, bedrock: BedrockClient
) -> Concert | None:
    """
    Llama a Bedrock para extraer información estructurada de conciertos
    de un mensaje de WhatsApp en lenguaje natural e informal.

    Args:
        message_text: Texto del mensaje a analizar.
        bedrock:      Cliente de Bedrock configurado.

    Returns:
        Objeto Concert si se encontró información válida, None si no.
    """
    prompt = f"""Analiza el siguiente mensaje de un grupo de WhatsApp sobre conciertos de metal en Colombia.

Mensaje: "{message_text}"

Tu tarea es determinar si este mensaje anuncia o confirma un concierto o festival de música metal.

Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional, sin markdown, sin explicaciones.

Formato de respuesta:
{{
    "is_concert_announcement": true o false,
    "band_name": "nombre exacto de la banda, o null si no se menciona",
    "festival_name": "nombre del festival si es festival, o null",
    "event_date": "YYYY-MM-DD si la fecha es clara, o null si es ambigua o no está",
    "city": "ciudad en Colombia (Bogotá, Medellín, Cali, etc.), o null",
    "venue": "nombre del venue/teatro/recinto, o null",
    "ticket_price_cop": número entero en pesos colombianos, o null,
    "ticket_url": "url si se menciona, o null",
    "is_cancellation": true si el mensaje anuncia una cancelación,
    "genres": ["lista de géneros de metal mencionados o inferibles"],
    "confidence": número entre 0.0 y 1.0 indicando qué tan seguro estás,
    "notes": "cualquier info adicional relevante en español"
}}

Reglas importantes:
- Solo marca is_concert_announcement como true si hay un evento FUTURO confirmado o muy probable
- Las noticias sobre discos, videos o polémicas de bandas NO son anuncios de conciertos
- Las preguntas como "¿alguien va?" tampoco son anuncios
- Si el mensaje es sobre una cancelación, marca is_cancellation como true
- Interpreta fechas en español informal: "15 de marzo" = próximo 15 de marzo
- confidence >= 0.7 significa que estás bastante seguro de que es un anuncio válido"""

    try:
        response_text = bedrock.invoke(prompt, max_tokens=600)

        # Limpiar posibles artefactos de markdown
        clean_response = response_text.strip()
        if clean_response.startswith("```"):
            clean_response = clean_response.split("```")[1]
            if clean_response.startswith("json"):
                clean_response = clean_response[4:]

        data = json.loads(clean_response)

        # Validar que sea un anuncio válido con suficiente confianza
        if not data.get("is_concert_announcement"):
            return None

        if data.get("confidence", 0) < 0.7:
            logger.info(
                f"Mensaje descartado por baja confianza ({data.get('confidence')}): {message_text[:60]}..."
            )
            return None

        # Manejar cancelaciones
        if data.get("is_cancellation"):
            logger.info(
                f"Cancelación detectada: {data.get('band_name')} - se manejará por separado"
            )
            # TODO: Implementar lógica de cancelaciones (marcar concierto como cancelado en DynamoDB)
            return None

        # Construir el objeto Concert
        band_name = data.get("band_name") or data.get("festival_name")
        if not band_name:
            return None

        # Parsear fecha
        event_date_str = data.get("event_date")
        if not event_date_str:
            logger.info(f"No se encontró fecha en el mensaje: {message_text[:60]}...")
            return None

        from datetime import date

        try:
            event_date = date.fromisoformat(event_date_str)
        except ValueError:
            logger.warning(f"Fecha inválida: {event_date_str}")
            return None

        # Verificar que el evento sea futuro
        if event_date < date.today():
            return None

        return Concert(
            band_name=band_name,
            event_date=event_date,
            city=data.get("city") or "Colombia",
            country=DEFAULT_COUNTRY,
            source="whatsapp_colombia",
            source_tier=SourceTier.INFORMAL,
            venue=data.get("venue"),
            ticket_url=data.get("ticket_url"),
            ticket_price=data.get("ticket_price_cop"),
            ticket_currency="COP",
            confidence=data.get("confidence", 0.7),
            raw_text=message_text[:500],  # Guardamos el texto original para referencia
        )

    except json.JSONDecodeError as e:
        logger.warning(
            f"Respuesta de Bedrock no es JSON válido: {e}. Mensaje: {message_text[:60]}..."
        )
        return None
    except Exception as e:
        logger.error(f"Error procesando mensaje con LLM: {e}")
        return None


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _is_system_message(text: str) -> bool:
    """Detecta mensajes de sistema de WhatsApp (no son mensajes de usuarios)."""
    system_patterns = [
        "se unió usando el enlace",
        "fue añadido",
        "salió del grupo",
        "cambió el asunto del grupo",
        "cambió el ícono del grupo",
        "Los mensajes y llamadas están cifrados",
        "Messages and calls are end-to-end encrypted",
    ]
    return any(pattern.lower() in text.lower() for pattern in system_patterns)


def _notify_discord(new_count: int, bucket: str, key: str):
    """Notifica por Discord que se procesó un nuevo export de WhatsApp."""
    import urllib.request

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        return

    filename = key.split("/")[-1]
    message = {
        "embeds": [
            {
                "title": "📱 Export de WhatsApp procesado",
                "description": (
                    f"Se procesó el archivo `{filename}` y se encontraron "
                    f"**{new_count} conciertos nuevos** en Colombia 🇨🇴\n\n"
                    "El Orchestrator Agent buscará vuelos y alojamiento para estos eventos."
                ),
                "color": 0xFF6600,
                "footer": {"text": "Metal Travel Tracker — WhatsApp Colombia"},
            }
        ]
    }

    try:
        data = json.dumps(message).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning(f"No se pudo enviar notificación a Discord: {e}")
