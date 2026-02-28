"""
plugins/bandsintown.py
----------------------
Plugin para la API de Bandsintown.
Complementa a Songkick con cobertura adicional, especialmente útil para
artistas que tienen mayor presencia en Bandsintown que en Songkick.

Documentación API: https://app.swaggerhub.com/apis/Bandsintown/PublicAPI/3.0.0
Nota: Bandsintown requiere un APP_ID (nombre de tu app), no una API key secreta.
"""

import asyncio
import os
from datetime import date
from urllib.parse import quote

import httpx

from src.models.concert import Concert, Country, EventType, MetalGenre, SourceTier
from src.plugins.base import ConcertSourcePlugin

# Bandsintown funciona por nombre de artista, no por ciudad.
# Mantenemos una lista curada de bandas de metal a monitorear.
# Esta lista puede crecer y se puede almacenar en DynamoDB para actualizarla
# sin redeploy.
MONITORED_BANDS = [
    # Black Metal
    "Mayhem", "Emperor", "Immortal", "Satyricon", "1349", "Blood Fire Death",
    "Gorgoroth", "Marduk", "Dark Funeral", "Watain", "Behemoth", "Batushka",
    "Mgła", "Wolves in the Throne Room", "Deafheaven", "Batushka", "Satanic Warmaster",
    "Warmoon Lord", "Baxaxaxa", "Nargaroth", "Mortuary Drape", "Horna"
    # Death Metal
    "Death", "Obituary", "Cannibal Corpse", "Morbid Angel", "Deicide",
    "Suffocation", "Nile", "Dying Fetus", "Bolt Thrower", "Autopsy",
    "Carcass", "Napalm Death", "Entombed", "Dismember", "Messiah", "Grave"
    # Thrash Metal
    "Metallica", "Megadeth", "Slayer", "Anthrax", "Testament", "Exodus",
    "Death Angel", "Overkill", "Kreator", "Sodom", "Destruction", "Sepultura",
    "Nuclear Assault", "Flotsam and Jetsam",
    # Heavy Metal clásico
    "Iron Maiden", "Judas Priest", "Black Sabbath", "Dio", "Accept",
    "Saxon", "Motörhead", "Venom", "Diamond Head", "Ambush", "Eternal Champion",
    "Sumerlands", "High Spirits", "Savage Master", "Metal Church", "Crimson Glory"
    # War Metal
    "Blasphemy", "Bestial Warlust", "Archgoat", "Impiety", "Conqueror", "Revenge"
    # Bandas latinoamericanas relevantes
    "Sepultura", "Sarcófago", "Krisiun", "Nervosa", "Lacuna Coil",
    "Rata Blanca", "Helker", "Horcas", "Impurity"
]

# Países mapeados a códigos de localización para filtrar
COUNTRY_FILTERS = {
    Country.COLOMBIA:       ["Bogotá", "Medellín", "Colombia"],
    Country.CHILE:          ["Santiago", "Chile"],
    Country.BRAZIL:         ["São Paulo", "Rio de Janeiro", "Brazil", "Brasil"],
    Country.UNITED_STATES:  ["United States", "USA"],
    Country.MEXICO:         ["Mexico City", "Ciudad de México", "Mexico"],
    Country.FINLAND:        ["Helsinki", "Finland", "Finlandia"],
    Country.SPAIN:          ["Madrid", "Barcelona", "Spain", "España"],
}


class BandsintownPlugin(ConcertSourcePlugin):
    """
    Plugin de Bandsintown que busca conciertos por artista y filtra por país.
    
    Estrategia diferente a Songkick: en lugar de buscar por ciudad,
    busca los próximos shows de una lista curada de bandas de metal.
    Esto garantiza resultados relevantes de género.
    """

    def __init__(self):
        self._app_id = os.environ.get("BANDSINTOWN_APP_ID", "metal-travel-tracker")
        self._base_url = "https://rest.bandsintown.com"

    @property
    def source_name(self) -> str:
        return "bandsintown"

    @property
    def reliability_tier(self) -> SourceTier:
        return SourceTier.OFFICIAL

    @property
    def rate_limit_calls_per_minute(self) -> int:
        return 120

    async def fetch_concerts(
        self,
        countries: list[Country],
        genres: list[MetalGenre],
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        """
        Busca conciertos de bandas monitoreadas en los países de interés.
        Hace llamadas en paralelo para todas las bandas de la lista.
        """
        self.log_fetch_start(countries, from_date, to_date)

        # Armar filtros de localización para los países solicitados
        location_keywords = []
        for country in countries:
            location_keywords.extend(COUNTRY_FILTERS.get(country, []))

        concerts: list[Concert] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Procesar en lotes de 20 bandas para no saturar la API
            batch_size = 20
            for i in range(0, len(MONITORED_BANDS), batch_size):
                batch = MONITORED_BANDS[i:i + batch_size]
                tasks = [
                    self._fetch_artist_events(client, band, from_date, to_date)
                    for band in batch
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for result in results:
                    if isinstance(result, Exception):
                        self.log_error(result, "fetch_artist_events")
                        continue

                    # Filtrar eventos por países de interés
                    for event in result:
                        if self._is_in_target_countries(event, countries, location_keywords):
                            concerts.append(event)

                # Pequeña pausa entre lotes
                await asyncio.sleep(1.0)

        self.log_fetch_result(len(concerts))
        return concerts

    async def _fetch_artist_events(
        self,
        client: httpx.AsyncClient,
        artist_name: str,
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        """Obtiene los próximos eventos de un artista específico."""
        encoded_name = quote(artist_name)

        try:
            response = await client.get(
                f"{self._base_url}/artists/{encoded_name}/events",
                params={
                    "app_id": self._app_id,
                    "date": f"{from_date.strftime('%Y-%m-%d')},{to_date.strftime('%Y-%m-%d')}",
                },
            )

            if response.status_code == 404:
                # Artista no encontrado en Bandsintown, es normal
                return []

            response.raise_for_status()
            events_data = response.json()

            if not isinstance(events_data, list):
                return []

            concerts = []
            for event in events_data:
                concert = self._parse_event(event, artist_name)
                if concert:
                    concerts.append(concert)

            return concerts

        except httpx.HTTPStatusError:
            return []
        except Exception as e:
            self.log_error(e, f"artist: {artist_name}")
            return []

    def _parse_event(self, event: dict, band_name: str) -> Concert | None:
        """Convierte un evento de Bandsintown a nuestro modelo Concert."""
        try:
            date_str = event.get("datetime", "")[:10]  # "YYYY-MM-DDTHH:MM:SS" → "YYYY-MM-DD"
            if not date_str:
                return None
            event_date = date.fromisoformat(date_str)

            venue = event.get("venue", {})
            city       = venue.get("city", "")
            country_str = venue.get("country", "")
            venue_name = venue.get("name", "TBD")

            # Determinar Country enum
            country = self._map_country_string(country_str)
            if not country:
                return None

            # Determinar tipo de evento
            event_type = EventType.FESTIVAL if event.get("festival") else EventType.CONCERT

            return Concert(
                band_name=band_name,
                event_date=event_date,
                city=city,
                country=country,
                source=self.source_name,
                source_tier=self.reliability_tier,
                event_id=str(event.get("id", "")),
                event_type=event_type,
                venue=venue_name,
                ticket_url=event.get("url", ""),
                genres=[],
            )

        except (KeyError, ValueError) as e:
            self.log_error(e, "parse_event")
            return None

    def _map_country_string(self, country_str: str) -> Country | None:
        """Convierte el nombre de país de Bandsintown a nuestro enum Country."""
        mapping = {
            "Colombia": Country.COLOMBIA,
            "Chile": Country.CHILE,
            "Brazil": Country.BRAZIL,
            "Brasil": Country.BRAZIL,
            "United States": Country.UNITED_STATES,
            "Mexico": Country.MEXICO,
            "México": Country.MEXICO,
            "Finland": Country.FINLAND,
            "Finlandia": Country.FINLAND,
            "Spain": Country.SPAIN,
            "España": Country.SPAIN,
        }
        return mapping.get(country_str)

    def _is_in_target_countries(
        self,
        concert: Concert,
        target_countries: list[Country],
        location_keywords: list[str],
    ) -> bool:
        """Verifica si un concierto es en uno de los países de interés."""
        return concert.country in target_countries
