"""
agents/orchestrator/handler.py
-------------------------------
El Orchestrator Agent es el cerebro del sistema.
Es invocado por AWS Step Functions una vez al día y coordina a todos los demás agentes.

Responsabilidades:
  1. Decidir qué fuentes de conciertos consultar hoy
  2. Deduplicar y validar conciertos encontrados
  3. Priorizar conciertos para búsqueda de vuelos (los más próximos primero)
  4. Invocar al Flight Agent para conciertos sin vuelo buscado
  5. Invocar al Hotel Agent para los mejores deals de vuelos
  6. Invocar al Reporter Agent para generar y enviar el reporte diario

El Orchestrator usa el LLM para:
  - Clasificar si una banda es de metal cuando hay dudas
  - Priorizar qué eventos son más relevantes para notificar
  - Decidir si el día de hoy amerita notificación (evitar spam)
"""

import json
import logging
import os
from datetime import date, timedelta
from typing import Optional

import boto3

from src.models.concert import Concert, Country, MetalGenre
from src.plugins import get_active_plugins
from src.plugins.base import ConcertSourcePlugin
from src.shared.bedrock_client import BedrockClient
from src.shared.dynamodb_client import DynamoDBClient
from src.shared.secrets import load_secrets
from src.shared.user_config import UserPreferences, load_user_preferences

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Configuración de países y géneros desde variables de entorno
TARGET_COUNTRIES = [
    Country.COLOMBIA,
    Country.CHILE,
    Country.BRAZIL,
    Country.UNITED_STATES,
    Country.MEXICO,
    Country.FINLAND,
    Country.SPAIN,
    Country.NORWAY,
    Country.GERMANY,
    Country.GREECE,
    Country.ROMANIA,
]

TARGET_GENRES = [
    MetalGenre.BLACK_METAL,
    MetalGenre.DEATH_METAL,
    MetalGenre.WAR_METAL,
    MetalGenre.HEAVY_METAL,
    MetalGenre.THRASH_METAL,
]

# Buscar conciertos hasta 9 meses hacia adelante
SEARCH_DAYS_AHEAD = 270

# Solo buscar vuelos si el concierto es en al menos 14 días
MIN_DAYS_FOR_FLIGHT_SEARCH = 14


def lambda_handler(event: dict, context) -> dict:
    """
    Entry point del Orchestrator Agent.
    Invocado por Step Functions diariamente.
    """
    load_secrets()
    prefs = load_user_preferences()
    logger.info("Orchestrator Agent iniciado")
    logger.info(f"Evento: {json.dumps(event)}")
    logger.info(f"Watchlist: {len(prefs.watchlist_bands)} bandas monitoreadas")

    bedrock = BedrockClient()
    dynamodb = DynamoDBClient(table_name=os.environ["DYNAMODB_TABLE_CONCERTS"])
    lambda_client = boto3.client("lambda")

    results = {
        "date": date.today().isoformat(),
        "concerts_found": 0,
        "concerts_new": 0,
        "flights_searched": 0,
        "deals_found": 0,
        "notified": False,
    }

    # -------------------------------------------------------------------
    # PASO 1: Recolectar conciertos de todas las fuentes (Tier 1 y 2)
    # -------------------------------------------------------------------
    logger.info("Paso 1: Recolectando conciertos de fuentes externas")

    import asyncio

    all_concerts = asyncio.run(collect_all_concerts(TARGET_COUNTRIES, TARGET_GENRES))
    results["concerts_found"] = len(all_concerts)
    logger.info(
        f"Total conciertos recolectados (antes de deduplicar): {len(all_concerts)}"
    )

    # -------------------------------------------------------------------
    # PASO 2: Filtrar, clasificar y deduplicar
    # -------------------------------------------------------------------
    logger.info("Paso 2: Clasificando y filtrando conciertos de metal")

    metal_concerts = classify_and_filter(all_concerts, bedrock, prefs)
    logger.info(f"Conciertos de metal después del filtro: {len(metal_concerts)}")

    # Guardar nuevos conciertos en DynamoDB con scoring de watchlist
    new_count = 0
    watchlist_new_count = 0
    for concert in metal_concerts:
        w_score = prefs.watchlist_score(concert.band_name)
        w_match = w_score > 0
        if not dynamodb.exists(concert.unique_key):
            if dynamodb.save_concert(
                concert, watchlist_score=w_score, watchlist_match=w_match
            ):
                new_count += 1
                if w_match:
                    watchlist_new_count += 1
                    logger.info(
                        f"WATCHLIST MATCH: {concert.band_name} en {concert.city}, "
                        f"{concert.country.value} ({concert.event_date_str}) — score {w_score}"
                    )

    results["concerts_new"] = new_count
    results["watchlist_new"] = watchlist_new_count
    logger.info(
        f"Conciertos nuevos guardados: {new_count} ({watchlist_new_count} watchlist matches)"
    )

    # -------------------------------------------------------------------
    # PASO 3: Buscar vuelos para conciertos sin búsqueda previa
    # -------------------------------------------------------------------
    logger.info("Paso 3: Buscando vuelos para conciertos pendientes")

    concerts_needing_flights = dynamodb.get_concerts_needing_flight_search(
        days_ahead=SEARCH_DAYS_AHEAD
    )
    logger.info(
        f"Conciertos que necesitan búsqueda de vuelos: {len(concerts_needing_flights)}"
    )

    # Priorizar watchlist matches para la búsqueda de vuelos
    concerts_needing_flights.sort(
        key=lambda x: float(x.get("watchlist_score", 0)), reverse=True
    )

    flight_deals = []
    hotel_deals = []
    for concert_item in concerts_needing_flights[
        :5
    ]:  # Máximo 5 por ejecución (cada llamada tarda ~30s, Lambda tiene 15min)
        try:
            flight_result = lambda_client.invoke(
                FunctionName=os.environ["FLIGHT_AGENT_FUNCTION_NAME"],
                InvocationType="RequestResponse",
                Payload=json.dumps(
                    {
                        "concert_country": concert_item.get("country"),
                        "event_date": concert_item.get("event_date"),
                        "concert_ref": concert_item.get("sk"),
                    }
                ),
            )
            flight_data = json.loads(flight_result["Payload"].read())
            if flight_data.get("best_deal"):
                flight_deals.append(flight_data["best_deal"])
                results["flights_searched"] += 1

                # Buscar hotel para los conciertos con deal de vuelo confirmado
                city = concert_item.get("city", "")
                if city:
                    try:
                        hotel_result = lambda_client.invoke(
                            FunctionName=os.environ["HOTEL_AGENT_FUNCTION_NAME"],
                            InvocationType="RequestResponse",
                            Payload=json.dumps(
                                {
                                    "city": city,
                                    "country": concert_item.get("country"),
                                    "event_date": concert_item.get("event_date"),
                                    "concert_ref": concert_item.get("sk"),
                                }
                            ),
                        )
                        hotel_data = json.loads(hotel_result["Payload"].read())
                        if hotel_data.get("best_hotel"):
                            hotel_deals.append(hotel_data["best_hotel"])
                            logger.info(
                                f"Hotel encontrado en {city}: "
                                f"{hotel_data['best_hotel'].get('name')} "
                                f"— ${hotel_data['best_hotel'].get('price_per_night_usd')}/noche"
                            )
                    except Exception as e:
                        logger.error(f"Error invocando Hotel Agent para {city}: {e}")

            dynamodb.mark_flight_searched(concert_item.get("sk", ""))

        except Exception as e:
            logger.error(f"Error invocando Flight Agent: {e}")

    results["deals_found"] = len(flight_deals)
    results["hotels_found"] = len(hotel_deals)

    # -------------------------------------------------------------------
    # PASO 4: Decidir si notificar hoy
    # -------------------------------------------------------------------
    logger.info("Paso 4: Evaluando si hay algo para notificar")

    should_notify = decide_should_notify(
        new_concerts=new_count,
        flight_deals=flight_deals,
        bedrock=bedrock,
    )

    if should_notify:
        logger.info("Paso 5: Invocando Reporter Agent para generar notificación")
        try:
            lambda_client.invoke(
                FunctionName=os.environ["REPORTER_AGENT_FUNCTION_NAME"],
                InvocationType="Event",  # Async, no esperamos respuesta
                Payload=json.dumps(
                    {
                        "new_concerts_count": new_count,
                        "watchlist_new_count": watchlist_new_count,
                        "flight_deals": flight_deals,
                        "hotel_deals": hotel_deals,
                        "report_date": date.today().isoformat(),
                        "is_weekly_report": date.today().weekday() == 6,
                    }
                ),
            )
            results["notified"] = True
        except Exception as e:
            logger.error(f"Error invocando Reporter Agent: {e}")
    else:
        logger.info("No hay novedades relevantes para notificar hoy")

    logger.info(f"Orchestrator completado: {results}")
    return results


# ---------------------------------------------------------------------------
# Recolección de conciertos (todos los plugins en paralelo)
# ---------------------------------------------------------------------------


async def collect_all_concerts(
    countries: list[Country],
    genres: list[MetalGenre],
) -> list[Concert]:
    """
    Carga los plugins activos desde el registro central y los ejecuta en paralelo.
    Maneja errores individuales sin detener el proceso completo.
    """
    import asyncio

    from_date = date.today()
    to_date = date.today() + timedelta(days=SEARCH_DAYS_AHEAD)

    # Cargar plugins desde el registro (TicketmasterPlugin + SerpApiEventsPlugin)
    plugins: list[ConcertSourcePlugin] = get_active_plugins()

    if not plugins:
        logger.error(
            "No hay plugins disponibles. Verifica las API keys en Secrets Manager."
        )
        return []

    logger.info(f"Plugins activos: {[p.source_name for p in plugins]}")

    # Ejecutar todos en paralelo
    tasks = [
        plugin.fetch_concerts(countries, genres, from_date, to_date)
        for plugin in plugins
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_concerts = []
    for plugin, result in zip(plugins, results):
        if isinstance(result, Exception):
            logger.error(f"Plugin {plugin.source_name} falló: {result}")
            continue
        logger.info(f"Plugin {plugin.source_name}: {len(result)} conciertos")
        all_concerts.extend(result)

    return all_concerts


# ---------------------------------------------------------------------------
# Clasificación y filtrado con LLM
# ---------------------------------------------------------------------------


def classify_and_filter(
    concerts: list[Concert],
    bedrock: BedrockClient,
    prefs: Optional[UserPreferences] = None,
) -> list[Concert]:
    """
    Usa Bedrock para clasificar bandas desconocidas y filtrar las que no son metal.

    Para evitar llamadas innecesarias al LLM:
    1. Primero verifica si el nombre de la banda hace match con keywords obvios de metal
    2. Solo llama al LLM para bandas ambiguas
    """
    classified = []
    uncertain_bands = []

    for concert in concerts:
        # SerpAPI: el título del evento no es el nombre de la banda, por lo que el LLM
        # no puede clasificarlo correctamente ("Concierto Metal Bogotá" no es una banda).
        # Confiamos en que la query de búsqueda ya filtró por metal — incluir directamente.
        if concert.source == "serpapi_events":
            classified.append(concert)
            continue

        # Si el nombre de la banda o el venue tiene keywords de metal, es obvio
        band_lower = concert.band_name.lower()
        obvious_metal_keywords = [
            "metal",
            "death",
            "black",
            "thrash",
            "slayer",
            "sepultura",
            "obituary",
            "kreator",
            "morbid",
            "cannibal",
            "napalm",
            "venom",
        ]
        if any(kw in band_lower for kw in obvious_metal_keywords):
            classified.append(concert)
            continue

        # Bandas bien conocidas que queremos siempre incluir
        known_metal_bands = {
            "metallica",
            "megadeth",
            "anthrax",
            "iron maiden",
            "judas priest",
            "motörhead",
            "motorhead",
            "accept",
            "dio",
            "exodus",
            "testament",
            "overkill",
            "destruction",
            "sodom",
            "watain",
            "behemoth",
            "mgła",
        }
        # Agregar watchlist del usuario (siempre son metal por definición)
        if prefs:
            for wb in prefs.watchlist_bands:
                known_metal_bands.add(wb.lower().strip())
        if band_lower in known_metal_bands:
            classified.append(concert)
            continue

        # Banda ambigua → clasificar con LLM (en lote para eficiencia)
        uncertain_bands.append(concert)

    # Clasificar bandas inciertas en lotes de 10
    if uncertain_bands:
        logger.info(f"Clasificando {len(uncertain_bands)} bandas con LLM")
        band_names = list({c.band_name for c in uncertain_bands})  # Deduplicar nombres

        # Lote de clasificación
        batch_size = 15
        metal_bands_confirmed = set()

        import time

        for i in range(0, len(band_names), batch_size):
            batch = band_names[i : i + batch_size]
            confirmed = classify_bands_batch(batch, bedrock)
            metal_bands_confirmed.update(confirmed)
            # Pausa entre llamadas a Bedrock para evitar ThrottlingException
            if i + batch_size < len(band_names):
                time.sleep(3.0)

        # Agregar solo las que confirmó el LLM
        for concert in uncertain_bands:
            if concert.band_name in metal_bands_confirmed:
                classified.append(concert)

    # Deduplicar por unique_key
    seen = set()
    unique_concerts = []
    for concert in classified:
        if concert.unique_key not in seen:
            seen.add(concert.unique_key)
            unique_concerts.append(concert)

    return unique_concerts


def classify_bands_batch(band_names: list[str], bedrock: BedrockClient) -> set[str]:
    """
    Clasifica un lote de nombres de bandas y retorna cuáles son de metal.
    Usa un solo call a Bedrock para el lote (eficiencia de costo).
    """
    bands_list = "\n".join(f"- {name}" for name in band_names)

    prompt = f"""De la siguiente lista de nombres de bandas, identifica cuáles son bandas de:
black metal, death metal, war metal, heavy metal, o thrash metal.

Lista:
{bands_list}

Responde SOLO con JSON:
{{"metal_bands": ["nombre exacto de las bandas que SÍ son de metal"]}}

Incluye en la lista solo bandas que claramente pertenezcan a esos géneros.
Si no estás seguro de alguna, NO la incluyas."""

    try:
        response = bedrock.invoke(prompt, max_tokens=500, temperature=0.0)
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        data = json.loads(clean)
        return set(data.get("metal_bands", []))
    except Exception as e:
        logger.warning(f"Error en classify_bands_batch: {e}")
        # Fail-open: si Bedrock falla (throttling, etc.) incluimos todas las bandas
        # del batch en lugar de descartarlas. Es mejor tener falsos positivos que
        # perder conciertos válidos por error transitorio.
        return set(band_names)


# ---------------------------------------------------------------------------
# Decisión de notificación
# ---------------------------------------------------------------------------


def decide_should_notify(
    new_concerts: int,
    flight_deals: list[dict],
    bedrock: BedrockClient,
) -> bool:
    """
    Decide si vale la pena enviar notificación hoy.

    Reglas:
    - Siempre notifica si hay deals de vuelos (precio bajo histórico)
    - Notifica si hay conciertos nuevos en Finlandia (eventos raros y especiales)
    - No notifica si solo hay conciertos ya conocidos sin deals
    - Envía reporte semanal completo los domingos independientemente
    """
    from datetime import date

    today = date.today()

    # Reporte semanal los domingos siempre
    if today.weekday() == 6:  # 6 = domingo
        return True

    # Hay deals de vuelos → siempre notificar
    if flight_deals:
        return True

    # Hay conciertos nuevos → notificar
    if new_concerts > 0:
        return True

    return False
