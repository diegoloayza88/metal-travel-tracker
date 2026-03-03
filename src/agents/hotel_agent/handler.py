"""
agents/hotel_agent/handler.py
------------------------------
El Hotel Agent busca alojamiento cercano al venue del concierto usando
la API Affiliate de Booking.com.

Criterios de búsqueda:
  - Check-in: 1 día antes del concierto
  - Check-out: 1 día después del concierto
  - 1 adulto, 1 habitación
  - Ordenado por precio (menor primero)
  - Mínimo rating: 7.0 / 10.0

La API de Booking.com Affiliate requiere aprobación como partner.
Si no está disponible, se genera un link de búsqueda directo a Booking.com
con los parámetros correctos como fallback.
"""

import json
import logging
import os
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urlencode

import httpx

from src.models.concert import Hotel

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Check-in 1 día antes, check-out 1 día después del concierto
CHECKIN_DAYS_BEFORE  = 1
CHECKOUT_DAYS_AFTER  = 1

# Coordenadas aproximadas de ciudades principales para búsqueda por radio
CITY_COORDINATES: dict[str, tuple[float, float]] = {
    # Colombia
    "Bogotá":        (4.7110, -74.0721),
    "Medellín":      (6.2442, -75.5812),
    "Cali":          (3.4516, -76.5320),
    # Chile
    "Santiago":      (-33.4489, -70.6693),
    # Brasil
    "São Paulo":     (-23.5505, -46.6333),
    "Rio de Janeiro": (-22.9068, -43.1729),
    # USA
    "New York":      (40.7128, -74.0060),
    "Los Angeles":   (34.0522, -118.2437),
    "Miami":         (25.7617, -80.1918),
    "Chicago":       (41.8781, -87.6298),
    # México
    "Mexico City":   (19.4326, -99.1332),
    "Guadalajara":   (20.6597, -103.3496),
    # Finlandia
    "Helsinki":      (60.1699, 24.9384),
    "Tampere":       (61.4978, 23.7610),
    # España
    "Madrid":        (40.4168, -3.7038),
    "Barcelona":     (41.3851, 2.1734),
}

# Radio de búsqueda en km alrededor del centro de la ciudad
SEARCH_RADIUS_KM = 3


def lambda_handler(event: dict, context) -> dict:
    """
    Entry point del Hotel Agent.

    Event params:
        city:         Ciudad del concierto
        country:      Código de país (ej: "CO")
        event_date:   Fecha del concierto (YYYY-MM-DD)
        concert_ref:  ID de referencia del concierto
        venue:        Nombre del venue (opcional, para búsqueda más precisa)
    """
    logger.info(f"Hotel Agent iniciado: {json.dumps(event)}")

    city         = event.get("city", "")
    country_code = event.get("country", "")
    event_date_str = event.get("event_date", "")
    concert_ref  = event.get("concert_ref", "")

    if not city or not event_date_str:
        return {"error": "Faltan parámetros: city y event_date son requeridos"}

    try:
        event_date = date.fromisoformat(event_date_str)
    except ValueError:
        return {"error": f"Fecha inválida: {event_date_str}"}

    checkin  = event_date - timedelta(days=CHECKIN_DAYS_BEFORE)
    checkout = event_date + timedelta(days=CHECKOUT_DAYS_AFTER)

    # Intentar con la API de Booking si está configurada
    affiliate_id = os.environ.get("BOOKING_AFFILIATE_ID")
    hotels = []

    if affiliate_id:
        hotels = search_booking_api(city, country_code, checkin, checkout, affiliate_id)

    # Si no hay API o no retornó resultados, generar links directos
    if not hotels:
        hotels = generate_booking_links(city, country_code, checkin, checkout)

    if not hotels:
        return {"hotels": [], "best_hotel": None}

    # Ordenar por precio y retornar el mejor
    hotels_with_price = [h for h in hotels if h.price_per_night_usd > 0]
    if hotels_with_price:
        hotels_with_price.sort(key=lambda h: h.price_per_night_usd)
        best = hotels_with_price[0]
    else:
        best = hotels[0]

    best.concert_ref = concert_ref

    result = {
        "best_hotel": {
            "name":                  best.name,
            "city":                  best.city,
            "price_per_night_usd":   best.price_per_night_usd,
            "total_price_usd":       best.total_price_usd,
            "rating":                best.rating,
            "booking_url":           best.booking_url,
            "check_in":              best.check_in.isoformat(),
            "check_out":             best.check_out.isoformat(),
            "nights":                best.nights,
            "concert_ref":           concert_ref,
        },
        "hotels_found": len(hotels),
    }

    logger.info(
        f"Mejor hotel en {city}: {best.name} | "
        f"${best.price_per_night_usd}/noche | Rating: {best.rating}"
    )
    return result


# ---------------------------------------------------------------------------
# Booking.com Affiliate API
# ---------------------------------------------------------------------------

def search_booking_api(
    city: str,
    country_code: str,
    checkin: date,
    checkout: date,
    affiliate_id: str,
) -> list[Hotel]:
    """
    Busca hoteles usando la API Affiliate de Booking.com.
    Requiere aprobación previa en https://join.booking.com/affiliateprogram

    Documentación: https://developers.booking.com/affiliateprogram/
    """
    coords = CITY_COORDINATES.get(city)
    if not coords:
        logger.warning(f"Coordenadas no encontradas para: {city}")
        return []

    lat, lon = coords

    try:
        response = httpx.get(
            "https://distribution-xml.booking.com/2.0/json/hotels",
            params={
                "latitude":       lat,
                "longitude":      lon,
                "radius":         SEARCH_RADIUS_KM,
                "checkin":        checkin.strftime("%Y-%m-%d"),
                "checkout":       checkout.strftime("%Y-%m-%d"),
                "adults_number":  1,
                "room_number":    1,
                "rows":           10,
                "order_by":       "price",
                "min_review_score": 7.0,
                "currency":       "USD",
                "languagecode":   "es",
            },
            headers={
                "Authorization": f"Basic {affiliate_id}",
                "Content-Type":  "application/json",
            },
            timeout=20.0,
        )
        response.raise_for_status()
        data = response.json()

        hotels = []
        for item in data.get("result", []):
            hotel = _parse_booking_result(item, city, country_code, checkin, checkout)
            if hotel:
                hotels.append(hotel)

        return hotels

    except Exception as e:
        logger.error(f"Error en Booking API: {e}")
        return []


def _parse_booking_result(
    item: dict,
    city: str,
    country_code: str,
    checkin: date,
    checkout: date,
) -> Optional[Hotel]:
    """Parsea un resultado de la API de Booking al modelo Hotel."""
    try:
        from src.models.concert import Country as CountryEnum
        country_map = {
            "CO": CountryEnum.COLOMBIA, "CL": CountryEnum.CHILE,
            "BR": CountryEnum.BRAZIL,   "US": CountryEnum.UNITED_STATES,
            "MX": CountryEnum.MEXICO,   "FI": CountryEnum.FINLAND,
            "ES": CountryEnum.SPAIN,
        }
        country = country_map.get(country_code, CountryEnum.COLOMBIA)

        price_per_night = float(item.get("price_breakdown", {}).get("gross_price", 0))
        nights = (checkout - checkin).days

        return Hotel(
            name=item.get("hotel_name", "Hotel"),
            city=city,
            country=country,
            price_per_night_usd=price_per_night,
            total_price_usd=price_per_night * nights,
            check_in=checkin,
            check_out=checkout,
            rating=float(item.get("review_score", 0)),
            booking_url=item.get("url", ""),
        )
    except Exception as e:
        logger.warning(f"Error parseando hotel: {e}")
        return None


# ---------------------------------------------------------------------------
# Fallback: Links directos a Booking.com (sin API)
# ---------------------------------------------------------------------------

def generate_booking_links(
    city: str,
    country_code: str,
    checkin: date,
    checkout: date,
) -> list[Hotel]:
    """
    Genera links de búsqueda directa a Booking.com cuando no hay API disponible.
    El usuario puede hacer clic para ver opciones en tiempo real.

    Retorna un hotel "placeholder" con el link de búsqueda.
    """
    from src.models.concert import Country as CountryEnum
    country_map = {
        "CO": CountryEnum.COLOMBIA, "CL": CountryEnum.CHILE,
        "BR": CountryEnum.BRAZIL,   "US": CountryEnum.UNITED_STATES,
        "MX": CountryEnum.MEXICO,   "FI": CountryEnum.FINLAND,
        "ES": CountryEnum.SPAIN,
    }
    country = country_map.get(country_code, CountryEnum.COLOMBIA)

    params = urlencode({
        "ss":       city,
        "checkin":  checkin.strftime("%Y-%m-%d"),
        "checkout": checkout.strftime("%Y-%m-%d"),
        "group_adults": 1,
        "no_rooms": 1,
        "order":    "price",
    })
    booking_url = f"https://www.booking.com/searchresults.html?{params}"

    nights = (checkout - checkin).days

    return [Hotel(
        name=f"Ver hoteles disponibles en {city} →",
        city=city,
        country=country,
        price_per_night_usd=0.0,  # Precio desconocido sin API
        total_price_usd=0.0,
        check_in=checkin,
        check_out=checkout,
        rating=None,
        booking_url=booking_url,
    )]
