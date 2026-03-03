"""
plugins/songkick.py
-------------------
Plugin para la API oficial de Songkick.
Songkick tiene buena cobertura de artistas internacionales en todos los países
de interés, especialmente Chile, Brasil, España y Finlandia.

Documentación API: https://www.songkick.com/developer
Límite: ~1 llamada/segundo en plan gratuito.
"""

import asyncio
import os
from datetime import date
from typing import Optional

import httpx

from src.models.concert import Concert, Country, EventType, MetalGenre, SourceTier
from src.plugins.base import ConcertSourcePlugin

# Mapeo de países de interés a IDs de metro areas de Songkick
# Estos son los IDs de las ciudades principales por país
SONGKICK_METRO_IDS: dict[Country, list[tuple[str, str]]] = {
    Country.COLOMBIA: [
        ("7644", "Bogotá"),
        ("30572", "Medellín"),
        ("28661", "Cali"),
    ],
    Country.CHILE: [
        ("7583", "Santiago"),
    ],
    Country.BRAZIL: [
        ("7610", "São Paulo"),
        ("7562", "Rio de Janeiro"),
        ("28816", "Belo Horizonte"),
    ],
    Country.UNITED_STATES: [
        ("26330", "New York"),
        ("26473", "Los Angeles"),
        ("24426", "Chicago"),
        ("28863", "Miami"),
        ("28422", "Houston"),
    ],
    Country.MEXICO: [
        ("28932", "Mexico City"),
        ("28961", "Guadalajara"),
        ("28812", "Monterrey"),
    ],
    Country.FINLAND: [
        ("28584", "Helsinki"),
        ("30533", "Tampere"),
    ],
    Country.SPAIN: [
        ("28604", "Madrid"),
        ("28605", "Barcelona"),
    ],
}

# Tags de géneros de metal para filtrar en Songkick
METAL_GENRE_TAGS = [
    "metal",
    "black metal",
    "death metal",
    "heavy metal",
    "thrash metal",
    "war metal",
    "doom metal",
    "extreme metal",
    "speed metal",
]


class SongkickPlugin(ConcertSourcePlugin):
    """
    Plugin de Songkick para búsqueda de conciertos por ciudad/metro area.

    Estrategia: Para cada país de interés, consulta las principales ciudades
    y filtra los resultados por géneros de metal.
    """

    def __init__(self):
        self._api_key = os.environ.get("SONGKICK_API_KEY")
        if not self._api_key:
            raise EnvironmentError(
                "SONGKICK_API_KEY no está definida en las variables de entorno"
            )
        self._base_url = "https://api.songkick.com/api/3.0"

    # -------------------------------------------------------------------
    # Propiedades requeridas
    # -------------------------------------------------------------------

    @property
    def source_name(self) -> str:
        return "songkick"

    @property
    def reliability_tier(self) -> SourceTier:
        return SourceTier.OFFICIAL

    @property
    def rate_limit_calls_per_minute(self) -> int:
        return 60  # Conservador para no llegar al límite

    # -------------------------------------------------------------------
    # Metodo principal
    # -------------------------------------------------------------------

    async def fetch_concerts(
        self,
        countries: list[Country],
        genres: list[MetalGenre],
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        """Busca conciertos en Songkick para los países dados."""
        self.log_fetch_start(countries, from_date, to_date)
        concerts: list[Concert] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for country in countries:
                if country not in SONGKICK_METRO_IDS:
                    continue

                metro_areas = SONGKICK_METRO_IDS[country]

                # Buscar en paralelo todas las ciudades del país
                tasks = [
                    self._fetch_city_concerts(
                        client, metro_id, city_name, country, from_date, to_date
                    )
                    for metro_id, city_name in metro_areas
                ]
                city_results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in city_results:
                    if isinstance(result, Exception):
                        self.log_error(result, "fetch_city_concerts")
                        continue
                    concerts.extend(result)

        # Filtrar por géneros de metal
        filtered = self._filter_metal_concerts(concerts)
        self.log_fetch_result(len(filtered))
        return filtered

    # -------------------------------------------------------------------
    # Métodos privados
    # -------------------------------------------------------------------

    async def _fetch_city_concerts(
        self,
        client: httpx.AsyncClient,
        metro_id: str,
        city_name: str,
        country: Country,
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        """Consulta la API de Songkick para una ciudad específica."""
        concerts = []
        page = 1

        while True:
            params = {
                "apikey": self._api_key,
                "min_date": from_date.strftime("%Y-%m-%d"),
                "max_date": to_date.strftime("%Y-%m-%d"),
                "page": page,
                "per_page": 50,
            }

            try:
                response = await client.get(
                    f"{self._base_url}/metro_areas/{metro_id}/calendar.json",
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                results_page = data.get("resultsPage", {})
                events = results_page.get("results", {}).get("event", [])

                if not events:
                    break

                for event in events:
                    concert = self._parse_event(event, country, city_name)
                    if concert:
                        concerts.append(concert)

                # Paginación
                total_entries = results_page.get("totalEntries", 0)
                per_page = results_page.get("perPage", 50)
                if page * per_page >= total_entries:
                    break

                page += 1
                await asyncio.sleep(0.1)  # Rate limiting suave

            except httpx.HTTPStatusError as e:
                self.log_error(e, f"HTTP {e.response.status_code} en {city_name}")
                break
            except Exception as e:
                self.log_error(e, city_name)
                break

        return concerts

    def _parse_event(
        self,
        event: dict,
        country: Country,
        city_name: str,
    ) -> Optional[Concert]:
        """Convierte un evento de la API de Songkick a nuestro modelo Concert."""
        try:
            # Extraer fecha
            start = event.get("start", {})
            date_str = start.get("date")
            if not date_str:
                return None
            event_date = date.fromisoformat(date_str)

            # Extraer nombre del artista principal
            performances = event.get("performance", [])
            if not performances:
                return None
            headliner = next(
                (p for p in performances if p.get("billing") == "headline"),
                performances[0],
            )
            band_name = headliner.get("displayName", "").strip()
            if not band_name:
                return None

            # Extraer venue
            venue = event.get("venue", {})
            venue_name = venue.get("displayName", "TBD")

            # Determinar tipo de evento
            event_type_str = event.get("type", "Concert").lower()
            event_type = (
                EventType.FESTIVAL
                if "festival" in event_type_str
                else EventType.CONCERT
            )

            # URL del evento
            event_url = event.get("uri", "")

            return Concert(
                band_name=band_name,
                event_date=event_date,
                city=city_name,
                country=country,
                source=self.source_name,
                source_tier=self.reliability_tier,
                event_id=str(event.get("id", "")),
                event_type=event_type,
                venue=venue_name,
                ticket_url=event_url,
                genres=[],  # Songkick no devuelve géneros directamente
            )

        except (KeyError, ValueError) as e:
            self.log_error(e, "parse_event")
            return None

    def _filter_metal_concerts(self, concerts: list[Concert]) -> list[Concert]:
        """
        Filtra conciertos que sean de metal basándose en el nombre de la banda.

        Nota: Songkick no retorna géneros en la API pública, así que hacemos
        un filtro por keywords en el nombre. El Concert Agent complementa esto
        con una consulta adicional a Bedrock para clasificar bandas desconocidas.
        """
        # Por ahora retornamos todos y dejamos que el Concert Agent filtre
        # con el LLM. En una versión futura se puede mantener una allowlist
        # de bandas de metal conocidas en DynamoDB.
        return concerts
