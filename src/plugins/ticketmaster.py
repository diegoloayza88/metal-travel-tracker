"""
plugins/ticketmaster.py
-----------------------
Plugin para la Ticketmaster Discovery API v2.
Fuente oficial para conciertos de metal en US, México, España, Finlandia y el resto
de los países mapeados. La API es gratuita (5 000 calls/día, 200/hora).

Registro gratuito: https://developer-acct.ticketmaster.com/user/register
Documentación:     https://developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/

Variable de entorno requerida:
  TICKETMASTER_API_KEY  →  Clave obtenida en developer.ticketmaster.com
"""

import asyncio
import os
from datetime import date
from typing import Optional

import httpx

from src.models.concert import Concert, Country, EventType, MetalGenre, SourceTier
from src.plugins.base import ConcertSourcePlugin

# Códigos de país de Ticketmaster (ISO 3166-1 alpha-2)
TM_COUNTRY_CODES: dict[Country, str] = {
    Country.UNITED_STATES: "US",
    Country.MEXICO: "MX",
    Country.SPAIN: "ES",
    Country.FINLAND: "FI",
    Country.BRAZIL: "BR",
    Country.COLOMBIA: "CO",
    Country.CHILE: "CL",
    Country.NORWAY: "NO",
    Country.GERMANY: "DE",
    Country.GREECE: "GR",
    Country.ROMANIA: "RO",
}

# Ticketmaster usa clasificaciones jerárquicas.
# Buscamos con classificationName=metal para capturar todos los subgéneros.
# Como fallback también buscamos con keyword para países con menor indexación.
METAL_KEYWORDS_BY_COUNTRY: dict[Country, list[str]] = {
    Country.COLOMBIA: ["metal", "black metal", "death metal", "thrash"],
    Country.CHILE: ["metal", "black metal", "death metal", "thrash"],
    Country.BRAZIL: ["metal", "black metal", "death metal", "thrash"],
    Country.UNITED_STATES: [],  # Ticketmaster US indexa bien con classificationName
    Country.MEXICO: [],
    Country.SPAIN: ["metal"],
    Country.FINLAND: ["metal"],
    Country.NORWAY: ["metal", "black metal"],
    Country.GERMANY: ["metal", "black metal", "death metal"],
    Country.GREECE: ["metal", "black metal"],
    Country.ROMANIA: ["metal", "black metal", "death metal"],
}


class TicketmasterPlugin(ConcertSourcePlugin):
    """
    Plugin para la Ticketmaster Discovery API.

    Estrategia:
    1. Para cada país objetivo hace una búsqueda con classificationName=metal.
    2. Para países con cobertura más débil, complementa con búsquedas por keyword.
    3. Pagina automáticamente si hay más de 200 resultados.
    4. Deduplica por event_id antes de retornar.
    """

    def __init__(self):
        api_key = os.environ.get("TICKETMASTER_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "TICKETMASTER_API_KEY no configurada. "
                "Regístrate en developer.ticketmaster.com para obtener una clave gratuita."
            )
        self._api_key = api_key
        self._base_url = "https://app.ticketmaster.com/discovery/v2"

    # ------------------------------------------------------------------
    # Propiedades del plugin
    # ------------------------------------------------------------------

    @property
    def source_name(self) -> str:
        return "ticketmaster"

    @property
    def reliability_tier(self) -> SourceTier:
        return SourceTier.OFFICIAL

    @property
    def rate_limit_calls_per_minute(self) -> int:
        # 200 calls/hora = ~3/min. Usamos 5 para permitir pequeñas ráfagas.
        return 5

    # ------------------------------------------------------------------
    # Método principal
    # ------------------------------------------------------------------

    async def fetch_concerts(
        self,
        countries: list[Country],
        genres: list[MetalGenre],
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        self.log_fetch_start(countries, from_date, to_date)

        seen_ids: set[str] = set()
        concerts: list[Concert] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Procesar países de forma secuencial con pausa para respetar
            # el rate limit de Ticketmaster (200 calls/hora).
            for country in countries:
                if country not in TM_COUNTRY_CODES:
                    continue
                try:
                    result = await self._fetch_country_metal(
                        client, country, from_date, to_date
                    )
                    for concert in result:
                        if concert.event_id and concert.event_id not in seen_ids:
                            seen_ids.add(concert.event_id)
                            concerts.append(concert)
                except Exception as e:
                    self.log_error(e, f"fetch_country_metal {country.value}")
                # Pausa de 2s entre países para no saturar el rate limit
                await asyncio.sleep(2.0)

        self.log_fetch_result(len(concerts))
        return concerts

    # ------------------------------------------------------------------
    # Búsqueda por país
    # ------------------------------------------------------------------

    async def _fetch_country_metal(
        self,
        client: httpx.AsyncClient,
        country: Country,
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        """Busca conciertos de metal en un país con classificationName=metal."""
        country_code = TM_COUNTRY_CODES[country]

        base_params = {
            "apikey": self._api_key,
            "countryCode": country_code,
            "classificationName": "metal",
            "startDateTime": f"{from_date.isoformat()}T00:00:00Z",
            "endDateTime": f"{to_date.isoformat()}T23:59:59Z",
            "size": 200,
            "sort": "date,asc",
        }

        concerts = await self._fetch_all_pages(client, base_params, country)

        # Para países con cobertura más débil, complementar con keyword search
        extra_keywords = METAL_KEYWORDS_BY_COUNTRY.get(country, [])
        for keyword in extra_keywords:
            await asyncio.sleep(0.5)
            kw_params = dict(base_params)
            del kw_params["classificationName"]
            kw_params["keyword"] = keyword
            extra = await self._fetch_all_pages(client, kw_params, country)
            concerts.extend(extra)

        return concerts

    async def _fetch_all_pages(
        self,
        client: httpx.AsyncClient,
        params: dict,
        country: Country,
    ) -> list[Concert]:
        """Pagina automáticamente hasta 5 páginas para no saturar el rate limit."""
        concerts: list[Concert] = []

        try:
            resp = await client.get(f"{self._base_url}/events.json", params=params)

            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()

            concerts.extend(self._parse_page(data, country))

            page_info = data.get("page", {})
            total_pages = page_info.get("totalPages", 1)

            if total_pages > 1:
                await asyncio.sleep(1.0)  # Respetar rate limit
                extra_tasks = []
                for p in range(1, min(total_pages, 5)):
                    page_params = dict(params)
                    page_params["page"] = p
                    extra_tasks.append(
                        self._fetch_single_page(client, page_params, country)
                    )
                extra_results = await asyncio.gather(
                    *extra_tasks, return_exceptions=True
                )
                for r in extra_results:
                    if not isinstance(r, Exception):
                        concerts.extend(r)

        except httpx.HTTPStatusError as e:
            self.log_error(
                e, f"país: {country.value} | status: {e.response.status_code}"
            )
        except Exception as e:
            self.log_error(e, f"país: {country.value}")

        return concerts

    async def _fetch_single_page(
        self,
        client: httpx.AsyncClient,
        params: dict,
        country: Country,
    ) -> list[Concert]:
        try:
            resp = await client.get(f"{self._base_url}/events.json", params=params)
            resp.raise_for_status()
            return self._parse_page(resp.json(), country)
        except Exception as e:
            self.log_error(e, "paginación")
            return []

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_page(self, data: dict, country: Country) -> list[Concert]:
        events = data.get("_embedded", {}).get("events", [])
        concerts = []
        for event in events:
            c = self._parse_event(event, country)
            if c:
                concerts.append(c)
        return concerts

    def _parse_event(self, event: dict, country: Country) -> Optional[Concert]:
        try:
            event_id = event.get("id", "")
            event_name = event.get("name", "")

            # Fecha
            start = event.get("dates", {}).get("start", {})
            date_str = start.get("localDate", "")
            if not date_str:
                return None
            event_date = date.fromisoformat(date_str)

            # Venue y ciudad
            embedded = event.get("_embedded", {})
            venues = embedded.get("venues", [])
            venue_obj = venues[0] if venues else {}
            venue_name = venue_obj.get("name", "")
            city = venue_obj.get("city", {}).get("name", "")

            # Artista principal
            attractions = embedded.get("attractions", [])
            if attractions:
                band_name = attractions[0].get("name", event_name)
            else:
                band_name = event_name  # Nombre del evento como fallback

            # Tipo de evento
            event_type = (
                EventType.FESTIVAL if len(attractions) > 2 else EventType.CONCERT
            )

            # URL y precio
            ticket_url = event.get("url", "")
            price_ranges = event.get("priceRanges", [])
            ticket_price: Optional[float] = None
            ticket_currency: Optional[str] = None
            if price_ranges:
                ticket_price = price_ranges[0].get("min")
                ticket_currency = price_ranges[0].get("currency")

            # Géneros desde clasificaciones Ticketmaster
            genres = self._extract_genres(event)

            return Concert(
                band_name=band_name,
                event_date=event_date,
                city=city,
                country=country,
                source=self.source_name,
                source_tier=self.reliability_tier,
                event_id=f"tm_{event_id}",
                event_type=event_type,
                venue=venue_name,
                genres=genres,
                ticket_url=ticket_url,
                ticket_price=ticket_price,
                ticket_currency=ticket_currency,
                confidence=1.0,
            )

        except (KeyError, ValueError) as e:
            self.log_error(e, "parse_event")
            return None

    def _extract_genres(self, event: dict) -> list[MetalGenre]:
        genres: set[MetalGenre] = set()
        for classification in event.get("classifications", []):
            for field in [
                classification.get("genre", {}).get("name", ""),
                classification.get("subGenre", {}).get("name", ""),
            ]:
                name = field.lower()
                if "black" in name:
                    genres.add(MetalGenre.BLACK_METAL)
                if "death" in name:
                    genres.add(MetalGenre.DEATH_METAL)
                if "thrash" in name:
                    genres.add(MetalGenre.THRASH_METAL)
                if "war" in name:
                    genres.add(MetalGenre.WAR_METAL)
                if "heavy" in name or ("metal" in name and not genres):
                    genres.add(MetalGenre.HEAVY_METAL)
        return list(genres)
