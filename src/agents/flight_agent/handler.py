"""
agents/flight_agent/handler.py
-------------------------------
El Flight Agent busca vuelos desde Lima (LIM) hacia el país del concierto
y determina si el precio es un buen deal comparado con el histórico.

Fuentes de vuelos:
  1. Amadeus API  → Fuente primaria, gratuita, datos GDS (mismos que Google Flights)
  2. SerpAPI      → Fuente secundaria, scraping de Google Flights (pago, opcional)

Lógica de análisis de precios:
  - Guarda cada precio encontrado en DynamoDB (histórico de 90 días)
  - Calcula el promedio y percentil 25 de los últimos 60 días para esa ruta
  - Un vuelo es GOOD DEAL si está <= percentil 25
  - Un vuelo es EXCELLENT si está <= percentil 25 * 0.85

Aeropuerto de origen: LIM (Lima, Perú - Jorge Chávez)
"""

import json
import logging
import os
import statistics
from datetime import date, timedelta
from typing import Optional

import httpx

from src.models.concert import DealQuality, Flight
from src.shared.dynamodb_client import DynamoDBClient
from src.shared.secrets import load_secrets

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Aeropuerto de origen siempre Lima
ORIGIN_AIRPORT = "LIM"

# Mapeo de países a aeropuertos principales
COUNTRY_AIRPORTS: dict[str, list[str]] = {
    "CO": ["BOG", "MDE"],  # Bogotá, Medellín
    "CL": ["SCL"],  # Santiago
    "BR": ["GRU", "GIG"],  # São Paulo, Rio de Janeiro
    "US": ["MIA", "JFK", "LAX"],  # Miami, New York, Los Angeles
    "MX": ["MEX", "GDL"],  # Ciudad de México, Guadalajara
    "FI": ["HEL"],  # Helsinki
    "ES": ["MAD", "BCN"],  # Madrid, Barcelona
}

# Días antes del concierto para buscar vuelos (rango de fechas)
FLIGHT_SEARCH_DAYS_BEFORE = 3  # Salir 3 días antes del concierto
FLIGHT_SEARCH_DAYS_AFTER = 2  # Regresar 2 días después

# Precio máximo absoluto a considerar (en USD)
MAX_PRICE_USD = 2500


def lambda_handler(event: dict, context) -> dict:
    """
    Entry point del Flight Agent.
    Recibe información del concierto y retorna el mejor deal encontrado.

    Event params:
        concert_country: Código de país (ej: "CO")
        event_date:      Fecha del concierto (YYYY-MM-DD)
        concert_ref:     ID de referencia del concierto (para linking)
    """
    load_secrets()
    logger.info(f"Flight Agent iniciado: {json.dumps(event)}")

    country_code = event.get("concert_country", "")
    event_date_str = event.get("event_date", "")
    concert_ref = event.get("concert_ref", "")

    if not country_code or not event_date_str:
        return {
            "error": "Faltan parámetros: concert_country y event_date son requeridos"
        }

    try:
        event_date = date.fromisoformat(event_date_str)
    except ValueError:
        return {"error": f"Fecha inválida: {event_date_str}"}

    # Verificar que el concierto esté al menos a 14 días
    days_until = (event_date - date.today()).days
    if days_until < 14:
        logger.info(
            f"Concierto muy próximo ({days_until} días), saltando búsqueda de vuelos"
        )
        return {"skipped": True, "reason": "too_close"}

    dynamodb = DynamoDBClient(table_name=os.environ["DYNAMODB_TABLE_FLIGHTS"])

    # Calcular fechas de vuelo
    departure_date = event_date - timedelta(days=FLIGHT_SEARCH_DAYS_BEFORE)
    return_date = event_date + timedelta(days=FLIGHT_SEARCH_DAYS_AFTER)

    if departure_date < date.today() + timedelta(days=1):
        departure_date = date.today() + timedelta(days=1)

    # Obtener aeropuertos del país destino
    dest_airports = COUNTRY_AIRPORTS.get(country_code, [])
    if not dest_airports:
        return {"error": f"País no soportado: {country_code}"}

    # -------------------------------------------------------------------
    # Buscar vuelos en Amadeus (fuente primaria)
    # -------------------------------------------------------------------
    all_flights = []

    amadeus_token = get_amadeus_token()
    if amadeus_token:
        for dest_airport in dest_airports:
            flights = search_amadeus_flights(
                token=amadeus_token,
                origin=ORIGIN_AIRPORT,
                destination=dest_airport,
                departure_date=departure_date,
                return_date=return_date,
            )
            all_flights.extend(flights)
            logger.info(f"Amadeus: {len(flights)} vuelos LIM→{dest_airport}")

    # -------------------------------------------------------------------
    # Buscar en SerpAPI / Google Flights (fuente secundaria, si está configurada)
    # -------------------------------------------------------------------
    serp_api_key = os.environ.get("SERPAPI_KEY")
    if serp_api_key and not all_flights:
        logger.info("Amadeus sin resultados, intentando SerpAPI")
        for dest_airport in dest_airports[:1]:  # Solo el aeropuerto principal
            serp_flights = search_serpapi_flights(
                api_key=serp_api_key,
                origin=ORIGIN_AIRPORT,
                destination=dest_airport,
                departure_date=departure_date,
                return_date=return_date,
            )
            all_flights.extend(serp_flights)

    if not all_flights:
        logger.info(f"No se encontraron vuelos LIM→{country_code}")
        return {"best_deal": None, "flights_found": 0}

    # -------------------------------------------------------------------
    # Filtrar y analizar precios
    # -------------------------------------------------------------------
    # Filtrar por precio máximo
    valid_flights = [f for f in all_flights if f.price_usd <= MAX_PRICE_USD]

    # Guardar todos los precios en el histórico
    for flight in valid_flights:
        dynamodb.save_flight_price(flight)

    # Analizar si algún vuelo es un buen deal
    for flight in valid_flights:
        flight = analyze_deal_quality(flight, dynamodb)

    # Ordenar por calidad del deal y luego por precio
    deal_order = {
        DealQuality.EXCELLENT: 0,
        DealQuality.GOOD: 1,
        DealQuality.FAIR: 2,
        DealQuality.NORMAL: 3,
    }
    valid_flights.sort(key=lambda f: (deal_order[f.deal_quality], f.price_usd))

    best = valid_flights[0]
    best.concert_ref = concert_ref

    result = {
        "best_deal": {
            "origin": best.origin,
            "destination": best.destination,
            "departure_date": best.departure_date.isoformat(),
            "return_date": best.return_date.isoformat() if best.return_date else None,
            "price_usd": best.price_usd,
            "airline": best.airline,
            "booking_url": best.booking_url,
            "deal_quality": best.deal_quality.value,
            "discount_pct": best.discount_pct,
            "price_avg_60d": best.price_avg_60d,
            "concert_ref": concert_ref,
        },
        "flights_found": len(valid_flights),
        "is_good_deal": best.is_good_deal,
    }

    logger.info(
        f"Mejor vuelo LIM→{best.destination}: ${best.price_usd} USD "
        f"({best.deal_quality.value}, {best.discount_pct:.1f}% descuento)"
    )
    return result


# ---------------------------------------------------------------------------
# Amadeus API
# ---------------------------------------------------------------------------


def get_amadeus_token() -> Optional[str]:
    """Obtiene el token OAuth2 de Amadeus."""
    client_id = os.environ.get("AMADEUS_CLIENT_ID")
    client_secret = os.environ.get("AMADEUS_CLIENT_SECRET")

    if not client_id or not client_secret:
        logger.warning("Amadeus credentials no configuradas")
        return None

    try:
        response = httpx.post(
            "https://test.api.amadeus.com/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        return response.json()["access_token"]
    except Exception as e:
        logger.error(f"Error obteniendo token Amadeus: {e}")
        return None


def search_amadeus_flights(
    token: str,
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date,
) -> list[Flight]:
    """Busca vuelos en la API de Amadeus Flight Offers Search."""
    try:
        response = httpx.get(
            "https://test.api.amadeus.com/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "originLocationCode": origin,
                "destinationLocationCode": destination,
                "departureDate": departure_date.strftime("%Y-%m-%d"),
                "returnDate": return_date.strftime("%Y-%m-%d"),
                "adults": 1,
                "nonStop": False,
                "currencyCode": "USD",
                "max": 10,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        flights = []
        for offer in data.get("data", []):
            flight = parse_amadeus_offer(
                offer, origin, destination, departure_date, return_date
            )
            if flight:
                flights.append(flight)

        return flights

    except httpx.HTTPStatusError as e:
        logger.error(f"Amadeus HTTP error {e.response.status_code}: {e}")
        return []
    except Exception as e:
        logger.error(f"Error en búsqueda Amadeus: {e}")
        return []


def parse_amadeus_offer(
    offer: dict,
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date,
) -> Optional[Flight]:
    """Parsea una oferta de Amadeus al modelo Flight."""
    try:
        price = float(offer["price"]["total"])
        airline_codes = list(
            {
                seg["carrierCode"]
                for itin in offer.get("itineraries", [])
                for seg in itin.get("segments", [])
            }
        )
        airline = ", ".join(airline_codes)

        # Contar escalas
        stops = sum(
            len(itin.get("segments", [])) - 1 for itin in offer.get("itineraries", [])
        )

        return Flight(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            price_usd=price,
            airline=airline,
            booking_url="https://www.amadeus.com",  # Profundizar en v2
            source="amadeus",
            stops=stops,
        )
    except (KeyError, ValueError) as e:
        logger.warning(f"Error parseando oferta Amadeus: {e}")
        return None


# ---------------------------------------------------------------------------
# SerpAPI / Google Flights
# ---------------------------------------------------------------------------


def search_serpapi_flights(
    api_key: str,
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date,
) -> list[Flight]:
    """Busca vuelos usando SerpAPI (Google Flights)."""
    try:
        response = httpx.get(
            "https://serpapi.com/search",
            params={
                "engine": "google_flights",
                "departure_id": origin,
                "arrival_id": destination,
                "outbound_date": departure_date.strftime("%Y-%m-%d"),
                "return_date": return_date.strftime("%Y-%m-%d"),
                "currency": "USD",
                "hl": "es",
                "api_key": api_key,
                "adults": 1,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        flights = []
        for result in data.get("best_flights", []) + data.get("other_flights", []):
            price = result.get("price")
            if not price:
                continue

            airline = result.get("flights", [{}])[0].get("airline", "Unknown")
            booking_url = result.get("booking_token", "https://flights.google.com")
            stops = len(result.get("flights", [])) - 1

            flights.append(
                Flight(
                    origin=origin,
                    destination=destination,
                    departure_date=departure_date,
                    return_date=return_date,
                    price_usd=float(price),
                    airline=airline,
                    booking_url=f"https://www.google.com/flights?hl=es#{booking_url}",
                    source="serpapi_google_flights",
                    stops=max(0, stops),
                )
            )

        return flights

    except Exception as e:
        logger.error(f"Error en SerpAPI: {e}")
        return []


# ---------------------------------------------------------------------------
# Análisis de calidad del deal
# ---------------------------------------------------------------------------


def analyze_deal_quality(flight: Flight, dynamodb: DynamoDBClient) -> Flight:
    """
    Compara el precio del vuelo contra el histórico de 60 días para esa ruta.
    Asigna DealQuality y calcula el porcentaje de descuento.
    """
    historical_prices = dynamodb.get_historical_prices(
        origin=flight.origin,
        destination=flight.destination,
        lookback_days=60,
    )

    if len(historical_prices) < 5:
        # No hay suficiente histórico para analizar
        logger.info(
            f"Histórico insuficiente para {flight.origin}→{flight.destination} ({len(historical_prices)} puntos)"
        )
        return flight

    avg_price = statistics.mean(historical_prices)
    historical_prices.sort()
    p25_index = max(0, int(len(historical_prices) * 0.25) - 1)
    p25_price = historical_prices[p25_index]

    flight.price_avg_60d = round(avg_price, 2)
    flight.price_p25_60d = round(p25_price, 2)
    flight.discount_pct = round((1 - flight.price_usd / avg_price) * 100, 1)

    if flight.price_usd <= p25_price * 0.85:
        flight.deal_quality = DealQuality.EXCELLENT
    elif flight.price_usd <= p25_price:
        flight.deal_quality = DealQuality.GOOD
    elif flight.price_usd <= avg_price * 0.90:
        flight.deal_quality = DealQuality.FAIR
    else:
        flight.deal_quality = DealQuality.NORMAL

    return flight
