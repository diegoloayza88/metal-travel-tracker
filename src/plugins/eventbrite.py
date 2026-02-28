"""
plugins/eventbrite.py
---------------------
Plugin para la API de Eventbrite.
Eventbrite es especialmente útil para Latinoamérica porque los promotores
locales la usan frecuentemente para la venta de tickets.

Documentación: https://www.eventbrite.com/platform/api
Autenticación: Bearer token (API key personal)
"""

import asyncio
import os
from datetime import date, datetime, timezone
from typing import Optional

import httpx

from src.models.concert import Concert, Country, EventType, MetalGenre, SourceTier
from src.plugins.base import ConcertSourcePlugin

# Mapeo de países a slugs de Eventbrite
EVENTBRITE_COUNTRY_SLUGS: dict[Country, str] = {
    Country.COLOMBIA:      "CO",
    Country.CHILE:         "CL",
    Country.BRAZIL:        "BR",
    Country.UNITED_STATES: "US",
    Country.MEXICO:        "MX",
    Country.FINLAND:       "FI",
    Country.SPAIN:         "ES",
}

# Keywords para buscar en Eventbrite
METAL_SEARCH_TERMS = [
    "metal concert",
    "heavy metal",
    "black metal",
    "death metal",
    "thrash metal",
    "festival metal",
    "concierto metal",
    "festival heavy",
]


class EventbritePlugin(ConcertSourcePlugin):
    """
    Plugin de Eventbrite que busca eventos de metal por país y keywords.

    Estrategia: Hace búsquedas por términos de metal en cada país de interés
    y filtra los resultados por categoría de música.
    """

    # ID de categoría "Music" en Eventbrite
    MUSIC_CATEGORY_ID = "103"

    def __init__(self):
        self._api_key = os.environ.get("EVENTBRITE_API_KEY")
        if not self._api_key:
            raise EnvironmentError("EVENTBRITE_API_KEY no está definida")
        self._base_url = "https://www.eventbriteapi.com/v3"

    @property
    def source_name(self) -> str:
        return "eventbrite"

    @property
    def reliability_tier(self) -> SourceTier:
        return SourceTier.OFFICIAL

    @property
    def rate_limit_calls_per_minute(self) -> int:
        return 60

    async def fetch_concerts(
        self,
        countries: list[Country],
        genres: list[MetalGenre],
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        """Busca eventos de metal en Eventbrite por país y keywords."""
        self.log_fetch_start(countries, from_date, to_date)
        concerts: list[Concert] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for country in countries:
                country_code = EVENTBRITE_COUNTRY_SLUGS.get(country)
                if not country_code:
                    continue

                # Buscar con múltiples términos en paralelo
                tasks = [
                    self._search_events(client, term, country_code, country, from_date, to_date)
                    for term in METAL_SEARCH_TERMS
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, Exception):
                        self.log_error(result, f"search in {country_code}")
                        continue
                    concerts.extend(result)

        # Deduplicar por event_id
        seen_ids: set[str] = set()
        unique = []
        for c in concerts:
            if c.event_id and c.event_id not in seen_ids:
                seen_ids.add(c.event_id)
                unique.append(c)
            elif not c.event_id:
                unique.append(c)

        self.log_fetch_result(len(unique))
        return unique

    async def _search_events(
        self,
        client: httpx.AsyncClient,
        search_term: str,
        country_code: str,
        country: Country,
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        """Busca eventos con un término específico en un país."""
        concerts = []

        # Formatear fechas en ISO 8601 con timezone UTC (requerido por Eventbrite)
        start_str = datetime.combine(from_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = datetime.combine(to_date, datetime.min.time()).replace(
            tzinfo=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "q":                    search_term,
            "location.country":     country_code,
            "categories":           self.MUSIC_CATEGORY_ID,
            "start_date.range_start": start_str,
            "start_date.range_end":   end_str,
            "expand":               "venue",
            "page_size":            50,
        }

        try:
            response = await client.get(
                f"{self._base_url}/events/search/",
                headers={"Authorization": f"Bearer {self._api_key}"},
                params=params,
            )
            response.raise_for_status()
            data = response.json()

            for event in data.get("events", []):
                concert = self._parse_event(event, country)
                if concert:
                    concerts.append(concert)

        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429:  # Ignorar rate limit silenciosamente
                self.log_error(e, f"HTTP {e.response.status_code}")
        except Exception as e:
            self.log_error(e, f"search: {search_term}/{country_code}")

        return concerts

    def _parse_event(self, event: dict, country: Country) -> Optional[Concert]:
        """Convierte un evento de Eventbrite a nuestro modelo Concert."""
        try:
            # Nombre del evento (puede ser el festival o la banda)
            name = event.get("name", {}).get("text", "").strip()
            if not name:
                return None

            # Verificar que tenga keywords de metal
            if not self.filter_by_genre_keywords(name):
                description = event.get("description", {}).get("text", "")
                if not self.filter_by_genre_keywords(description):
                    return None

            # Fecha de inicio
            start = event.get("start", {})
            local_date_str = start.get("local", "")[:10]  # "YYYY-MM-DDTHH:MM:SS" → "YYYY-MM-DD"
            if not local_date_str:
                return None
            event_date = date.fromisoformat(local_date_str)

            # Venue
            venue_data = event.get("venue", {})
            city       = venue_data.get("address", {}).get("city", "")
            venue_name = venue_data.get("name", "TBD")

            # Tipo de evento
            is_festival = any(
                word in name.lower()
                for word in ["festival", "fest", "open air", "metal fest"]
            )
            event_type = EventType.FESTIVAL if is_festival else EventType.CONCERT

            # Precio (Eventbrite puede tener esta info)
            ticket_price = None
            is_free = event.get("is_free", False)
            if not is_free:
                ticket_price_info = event.get("ticket_availability", {})
                min_price = ticket_price_info.get("minimum_ticket_price", {})
                if min_price:
                    ticket_price = float(min_price.get("major_value", 0) or 0)

            return Concert(
                band_name=name,
                event_date=event_date,
                city=city or country.value,
                country=country,
                source=self.source_name,
                source_tier=self.reliability_tier,
                event_id=str(event.get("id", "")),
                event_type=event_type,
                venue=venue_name,
                ticket_url=event.get("url", ""),
                ticket_price=ticket_price if ticket_price else None,
                ticket_currency="USD",
                genres=[],
            )

        except (KeyError, ValueError) as e:
            self.log_error(e, "parse_event")
            return None
