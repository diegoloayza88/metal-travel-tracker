"""
agents/reporter_agent/handler.py
---------------------------------
El Reporter Agent recibe los deals y conciertos del día y genera un reporte
en lenguaje natural usando Bedrock. Luego lo envía por SMS, Email y Discord.

Es el último eslabón de la cadena y el que Diego ve directamente.
El objetivo es que el reporte suene como un mensaje de un amigo metalero
que encontró buenas opciones, no como un reporte corporativo aburrido.
"""

import json
import logging
import os
from datetime import date

from src.shared.bedrock_client import BedrockClient
from src.shared.dynamodb_client import DynamoDBClient
from src.shared.notifications import NotificationService
from src.shared.secrets import load_secrets
from src.shared.user_config import load_user_preferences

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Perfil del usuario para personalizar el reporte
USER_PROFILE = """
Usuario: Diego, metalero de Lima, Perú.
Géneros favoritos: black metal, death metal, war metal, heavy metal, thrash metal.
Países de interés: Colombia, Chile, Brasil, Estados Unidos, México, Finlandia, España.
Origen de viajes: Lima, Perú (LIM).
Idioma: español latinoamericano, puede usar términos del metal en inglés.
Tono preferido: entusiasta pero directo, como un amigo metalero informándote.
"""


def lambda_handler(event: dict, context) -> dict:
    """
    Entry point del Reporter Agent.

    Event params:
        new_concerts_count: Cantidad de conciertos nuevos encontrados hoy.
        flight_deals:       Lista de deals de vuelos (pueden estar vacíos).
        report_date:        Fecha del reporte (YYYY-MM-DD).
        is_weekly_report:   True si es el reporte completo del domingo.
    """
    logger.info(f"Reporter Agent iniciado: {json.dumps(event)}")

    load_secrets()
    prefs = load_user_preferences()
    bedrock = BedrockClient()
    dynamodb = DynamoDBClient(table_name=os.environ["DYNAMODB_TABLE_CONCERTS"])
    notifier = NotificationService()

    new_concerts_count = event.get("new_concerts_count", 0)
    watchlist_new_count = event.get("watchlist_new_count", 0)
    flight_deals = event.get("flight_deals", [])
    report_date = event.get("report_date", date.today().isoformat())
    is_weekly = event.get("is_weekly_report", False)

    # -------------------------------------------------------------------
    # Recopilar datos para el reporte
    # -------------------------------------------------------------------

    # Todos los países monitoreados (incluye festivales en Europa/Grecia/Noruega)
    all_country_codes = ["CO", "CL", "BR", "US", "MX", "FI", "ES", "NO", "DE", "GR"]

    # Si es reporte semanal, traer todos los conciertos próximos
    upcoming_concerts = []
    if is_weekly:
        for country_code in all_country_codes:
            concerts = dynamodb.get_upcoming_concerts(
                country=country_code,
                days_ahead=270,
                min_confidence=0.7,
            )
            upcoming_concerts.extend(concerts[:8])  # Top 8 por país

    # Siempre traer watchlist matches para destacarlos
    watchlist_concerts = []
    for country_code in all_country_codes:
        concerts = dynamodb.get_upcoming_concerts(
            country=country_code,
            days_ahead=270,
            min_confidence=0.5,
        )
        for c in concerts:
            if c.get("watchlist_match") or float(c.get("watchlist_score", 0)) > 0:
                watchlist_concerts.append(c)

    watchlist_concerts.sort(
        key=lambda x: float(x.get("watchlist_score", 0)), reverse=True
    )

    # -------------------------------------------------------------------
    # Generar reporte con Bedrock
    # -------------------------------------------------------------------
    report_text = generate_report(
        bedrock=bedrock,
        flight_deals=flight_deals,
        new_concerts_count=new_concerts_count,
        watchlist_new_count=watchlist_new_count,
        upcoming_concerts=upcoming_concerts,
        watchlist_concerts=watchlist_concerts,
        prefs=prefs,
        report_date=report_date,
        is_weekly=is_weekly,
    )

    logger.info(f"Reporte generado ({len(report_text)} caracteres)")

    # -------------------------------------------------------------------
    # Enviar notificaciones
    # -------------------------------------------------------------------
    results = {}

    # SMS (versión corta)
    sms_text = generate_sms_summary(flight_deals, new_concerts_count)
    results["sms"] = notifier.send_sms(sms_text)

    # Email (reporte completo)
    email_subject = build_email_subject(flight_deals, new_concerts_count, report_date)
    results["email"] = notifier.send_email(
        subject=email_subject,
        body_text=report_text,
        body_html=markdown_to_html(report_text),
    )

    # Discord (reporte formateado para Discord)
    discord_message = build_discord_message(report_text, flight_deals, report_date)
    results["discord"] = notifier.send_discord(discord_message)

    logger.info(f"Notificaciones enviadas: {results}")

    return {
        "statusCode": 200,
        "report_generated": True,
        "notifications": results,
        "report_length": len(report_text),
    }


# ---------------------------------------------------------------------------
# Generación del reporte con LLM
# ---------------------------------------------------------------------------


def generate_report(
    bedrock: BedrockClient,
    flight_deals: list[dict],
    new_concerts_count: int,
    watchlist_new_count: int,
    upcoming_concerts: list[dict],
    watchlist_concerts: list[dict],
    prefs,
    report_date: str,
    is_weekly: bool,
) -> str:
    """
    Usa Bedrock para generar un reporte personalizado en lenguaje natural.
    Incluye watchlist matches, presupuestos desde Lima y ventanas óptimas de compra.
    """
    from src.shared.user_config import (
        FLIGHT_ESTIMATE_USD,
        HOTEL_ESTIMATE_USD,
        BUY_WINDOW_FLIGHTS,
    )

    # Serializar deals de vuelos
    deals_summary = (
        json.dumps(flight_deals, indent=2, ensure_ascii=False)
        if flight_deals
        else "Ninguno"
    )

    # Serializar watchlist matches con contexto de presupuesto
    watchlist_summary = ""
    if watchlist_concerts:
        enriched = []
        for c in watchlist_concerts[:15]:
            country = c.get("country", "")
            flight_est = FLIGHT_ESTIMATE_USD.get(country, (500, 1000))
            hotel_est = HOTEL_ESTIMATE_USD.get(country, (80, 150))
            buy_window = BUY_WINDOW_FLIGHTS.get(country, "8-12 semanas antes")
            enriched.append(
                {
                    "banda": c.get("band_name", ""),
                    "score_watchlist": float(c.get("watchlist_score", 0)),
                    "fecha": c.get("event_date", ""),
                    "ciudad": c.get("city", ""),
                    "país": country,
                    "festival": c.get("festival_name", ""),
                    "venue": c.get("venue", ""),
                    "ticket_url": c.get("ticket_url", ""),
                    "presupuesto_vuelo_usd": f"${flight_est[0]}-${flight_est[1]}",
                    "presupuesto_hotel_3noches_usd": f"${hotel_est[0]*3}-${hotel_est[1]*3}",
                    "total_estimado_usd": f"${flight_est[0] + hotel_est[0]*3}-${flight_est[1] + hotel_est[1]*3}",
                    "mejor_momento_comprar_vuelo": buy_window,
                }
            )
        watchlist_summary = json.dumps(enriched, indent=2, ensure_ascii=False)

    # Serializar conciertos próximos generales
    concerts_summary = ""
    if upcoming_concerts:
        concerts_summary = json.dumps(
            [
                {
                    "banda": c.get("band_name", ""),
                    "fecha": c.get("event_date", ""),
                    "ciudad": c.get("city", ""),
                    "país": c.get("country", ""),
                    "festival": c.get("festival_name", ""),
                    "venue": c.get("venue", ""),
                    "fuente": c.get("source", ""),
                    "es_watchlist": bool(c.get("watchlist_match")),
                }
                for c in upcoming_concerts[:25]
            ],
            indent=2,
            ensure_ascii=False,
        )

    # Presupuesto de referencia por país (para el reporte semanal)
    budget_table = "\n".join(
        f"  {cc}: vuelo ${v[0]}-${v[1]} | hotel 3n ${h[0]*3}-${h[1]*3} | comprar vuelo: {BUY_WINDOW_FLIGHTS.get(cc, 'N/A')}"
        for cc, v in FLIGHT_ESTIMATE_USD.items()
        for h in [HOTEL_ESTIMATE_USD.get(cc, (80, 150))]
        if cc != "PE"
    )

    report_type = "REPORTE SEMANAL COMPLETO" if is_weekly else "ALERTA DIARIA"

    system_prompt = f"""Eres el asistente personal de metal travel para Diego.
{USER_PROFILE}

Tu trabajo es generar reportes emocionantes, directos y útiles sobre conciertos de metal
y oportunidades de viaje. Escribe como un amigo metalero apasionado, no como un sistema automatizado.
Usa emojis con moderación (solo los que añadan valor).
El reporte debe estar en español latinoamericano.
Máximo 900 palabras para reportes diarios, 1500 para semanales."""

    watchlist_section = (
        f"""
BANDAS DE TU WATCHLIST DETECTADAS ({len(watchlist_concerts)} eventos):
{watchlist_summary if watchlist_summary else "Ninguna esta semana"}
"""
        if watchlist_concerts or is_weekly
        else ""
    )

    upcoming_section = (
        f"""
OTROS CONCIERTOS EN EL RADAR:
{concerts_summary}
"""
        if upcoming_concerts
        else ""
    )

    budget_section = (
        f"""
REFERENCIA DE PRESUPUESTOS DESDE LIMA (LIM):
{budget_table}
"""
        if is_weekly
        else ""
    )

    prompt = f"""Genera un {report_type} de Metal Travel Tracker para el {report_date}.

DEALS DE VUELOS ENCONTRADOS:
{deals_summary}

CONCIERTOS NUEVOS DETECTADOS HOY: {new_concerts_count} total ({watchlist_new_count} de tu watchlist)
{watchlist_section}{upcoming_section}{budget_section}

INSTRUCCIONES PARA EL REPORTE:
1. PRIORIDAD MÁXIMA — Si hay deals EXCELLENT o GOOD de vuelos, ponlos PRIMERO con entusiasmo real.
   Incluye: precio exacto, ruta, fechas, % descuento vs promedio, y por qué actuar ya.

2. WATCHLIST — Destaca SIEMPRE las bandas de la watchlist de Diego (score > 0).
   Para cada una incluye:
   - Nombre de la banda, fecha, ciudad y país
   - Si es parte de un festival, menciona el festival
   - Presupuesto estimado de viaje desde Lima (vuelo + hotel 3 noches)
   - Cuándo comprar el vuelo para esa fecha (usar "mejor_momento_comprar_vuelo")
   - Link de tickets si está disponible

3. FESTIVALES — Para los festivales monitoreados (Steelfest, Keep It True, Inferno, etc.),
   indica si el lineup ya está anunciado o si está por confirmar, y el costo estimado del viaje.

4. RADAR GENERAL — Si hay conciertos interesantes fuera de watchlist, mencionarlos brevemente.

5. ACCIÓN CONCRETA — Termina SIEMPRE con 1-3 acciones específicas que Diego puede tomar HOY
   (comprar tickets, reservar vuelo, activar alertas de precio, etc.).

6. NUNCA inventes precios ni fechas. Usa solo los datos proporcionados arriba.
7. Precios siempre en USD. Links de reserva cuando estén disponibles.

Estructura del reporte:
- Encabezado con fecha y tipo de reporte
- Sección de deals de vuelos (si los hay)
- Sección de watchlist matches (si los hay)
- Sección de festivales del año
- Radar general (brevemente)
- Acciones para hoy"""

    try:
        report = bedrock.invoke(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=2000,
            temperature=0.4,
        )
        return report
    except Exception as e:
        logger.error(f"Error generando reporte con Bedrock: {e}")
        return generate_fallback_report(flight_deals, new_concerts_count, report_date)


def generate_fallback_report(
    flight_deals: list[dict],
    new_concerts_count: int,
    report_date: str,
) -> str:
    """Reporte básico de texto plano cuando Bedrock falla."""
    lines = [
        f"🤘 METAL TRAVEL TRACKER — {report_date}",
        "",
        f"Conciertos nuevos detectados: {new_concerts_count}",
        "",
    ]

    if flight_deals:
        lines.append("DEALS DE VUELOS:")
        for deal in flight_deals:
            lines.append(
                f"  • {deal.get('origin')} → {deal.get('destination')}: "
                f"${deal.get('price_usd')} USD | "
                f"{deal.get('deal_quality')} | "
                f"{deal.get('discount_pct', 0):.1f}% descuento"
            )
            if deal.get("booking_url"):
                lines.append(f"    Reservar: {deal['booking_url']}")
    else:
        lines.append("Sin deals de vuelos destacados hoy.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers de formato para cada canal
# ---------------------------------------------------------------------------


def generate_sms_summary(flight_deals: list[dict], new_concerts: int) -> str:
    """
    Genera un SMS corto (máximo 160 caracteres).
    SMS no soporta formato, solo texto plano.
    """
    if flight_deals:
        best = flight_deals[0]
        dest = best.get("destination", "")
        price = best.get("price_usd", 0)
        quality = best.get("deal_quality", "")
        return (
            f"Metal Travel: {quality} deal LIM-{dest} ${price}USD. "
            f"{new_concerts} conciertos nuevos. Ver email para detalles."
        )[:160]
    elif new_concerts > 0:
        return f"Metal Travel: {new_concerts} conciertos nuevos detectados. Ver email para detalles."
    else:
        return "Metal Travel: Sin novedades relevantes hoy."


def build_email_subject(
    flight_deals: list[dict],
    new_concerts: int,
    report_date: str,
) -> str:
    """Construye el asunto del email según el contenido del reporte."""
    if flight_deals:
        best = flight_deals[0]
        quality = best.get("deal_quality", "DEAL")
        dest = best.get("destination", "")
        price = best.get("price_usd", 0)
        return f"🤘 {quality}: LIM→{dest} ${price} USD — {report_date}"
    elif new_concerts > 0:
        return f"🤘 {new_concerts} conciertos nuevos en tu radar — {report_date}"
    else:
        return f"🤘 Metal Travel Tracker — Reporte {report_date}"


def build_discord_message(
    report_text: str, flight_deals: list[dict], report_date: str
) -> dict:
    """
    Construye el payload del webhook de Discord con embeds.
    Discord soporta Markdown y embeds con color.
    """
    # Color del embed según calidad del mejor deal
    embed_color = 0x808080  # Gris por defecto
    if flight_deals:
        best_quality = flight_deals[0].get("deal_quality", "NORMAL")
        if best_quality == "EXCELLENT":
            embed_color = 0xFF0000  # Rojo metal para EXCELLENT
        elif best_quality == "GOOD":
            embed_color = 0xFF6600  # Naranja para GOOD
        elif best_quality == "FAIR":
            embed_color = 0xFFCC00  # Amarillo para FAIR

    # Discord tiene límite de 4096 chars por embed description
    description = report_text[:4000] if len(report_text) > 4000 else report_text

    payload = {
        "username": "Metal Travel Tracker 🤘",
        "avatar_url": "https://i.imgur.com/metal_placeholder.png",
        "embeds": [
            {
                "title": f"🤘 Metal Travel Report — {report_date}",
                "description": description,
                "color": embed_color,
                "footer": {"text": "Metal Travel Tracker • Lima, Perú → El mundo"},
            }
        ],
    }

    # Agregar fields individuales para cada deal (más visual en Discord)
    if flight_deals:
        fields = []
        for deal in flight_deals[:3]:  # Máximo 3 deals como fields
            fields.append(
                {
                    "name": f"✈️ LIM → {deal.get('destination')} | {deal.get('deal_quality')}",
                    "value": (
                        f"**${deal.get('price_usd')} USD** "
                        f"({deal.get('discount_pct', 0):.1f}% bajo promedio)\n"
                        f"🗓️ Salida: {deal.get('departure_date')} | Regreso: {deal.get('return_date', 'N/A')}\n"
                        f"✈️ {deal.get('airline', 'N/A')}\n"
                        f"[Reservar aquí]({deal.get('booking_url', '#')})"
                    ),
                    "inline": False,
                }
            )
        payload["embeds"][0]["fields"] = fields

    return payload


def markdown_to_html(text: str) -> str:
    """
    Conversión básica de Markdown a HTML para el email.
    Para producción considera usar la librería `markdown` de Python.
    """
    import re

    html = text
    # Headers
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
    # Bold
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    # Saltos de línea
    html = html.replace("\n", "<br>\n")
    return f"""
    <html><body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="background: #1a1a1a; color: #f0f0f0; padding: 20px; border-radius: 8px;">
    {html}
    </div>
    <p style="color: #666; font-size: 12px; margin-top: 20px;">
    Metal Travel Tracker • Lima, Perú → El mundo 🤘
    </p>
    </body></html>
    """
