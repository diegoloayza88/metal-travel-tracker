"""
plugins/serpapi_events.py
--------------------------
Plugin que usa SerpAPI Google Events para descubrir conciertos de metal.

Reutiliza la misma SERPAPI_KEY ya configurada en el sistema para vuelos.
Es especialmente potente para países con menor cobertura en APIs oficiales:
Colombia, Chile y Brasil.

Documentación: https://serpapi.com/google-events-api
Precio:        La misma cuenta que se usa para vuelos.

Variable de entorno requerida:
  SERPAPI_KEY  →  Compartida con el flight_agent
"""

import asyncio
import os
import re
from datetime import date, datetime
from typing import Optional

import httpx

from src.models.concert import Concert, Country, EventType, MetalGenre, SourceTier
from src.plugins.base import ConcertSourcePlugin

# ──────────────────────────────────────────────────────────────────────────────
# Configuración de búsqueda por país
# ──────────────────────────────────────────────────────────────────────────────

# Cada país tiene un código Google (gl), idioma (hl) y lista de queries.
# Usamos 2 queries por país para no gastar demasiados créditos de API
# mientras cubrimos los géneros principales: black/death metal + thrash.
COUNTRY_SEARCH_CONFIG: dict[Country, dict] = {
    Country.COLOMBIA: {
        "gl": "co",
        "hl": "es",
        "queries": ["concierto black metal Colombia", "death metal Colombia"],
    },
    Country.CHILE: {
        "gl": "cl",
        "hl": "es",
        "queries": ["concierto metal Chile", "black metal Chile"],
    },
    Country.BRAZIL: {
        "gl": "br",
        "hl": "pt",
        "queries": ["show metal Brasil", "black metal show Brasil"],
    },
    Country.UNITED_STATES: {
        "gl": "us",
        "hl": "en",
        "queries": ["black metal concert USA", "death metal thrash concert"],
    },
    Country.MEXICO: {
        "gl": "mx",
        "hl": "es",
        "queries": ["concierto metal Mexico", "black metal Mexico"],
    },
    Country.FINLAND: {
        "gl": "fi",
        "hl": "fi",
        "queries": ["metal keikka Suomi", "black metal concert Finland"],
    },
    Country.SPAIN: {
        "gl": "es",
        "hl": "es",
        "queries": ["concierto metal España", "black death metal España"],
    },
}

# Keywords usados para pre-filtrar resultados de Google Events
# (muchos resultados no serán de metal)
METAL_FILTER_KEYWORDS = {
    "metal", "thrash", "black", "death", "grindcore", "doom", "heavy",
    "carcass", "slayer", "sepultura", "kreator", "cannibal", "napalm",
    "morbid", "obituary", "venom", "mayhem", "behemoth", "watain",
    "mgła", "marduk", "dark funeral", "immortal", "satyricon",
    "metallica", "megadeth", "anthrax", "exodus", "testament", "overkill",
    "sodom", "destruction", "iron maiden", "judas priest",
}


class SerpApiEventsPlugin(ConcertSourcePlugin):
    """
    Plugin de Google Events via SerpAPI.

    Estrategia:
    1. Para cada país lanza 2 queries de búsqueda en Google Events.
    2. Pre-filtra resultados con keywords de metal antes de parsearlos.
    3. El LLM del Orchestrator hace la clasificación final de bandas ambiguas.
    4. Deduplica por (título normalizado + fecha + país).

    Cobertura ideal: CO, CL, BR (donde Ticketmaster no tiene tanta profundidad).
    También cubre US, MX, ES, FI como fuente secundaria.
    """

    def __init__(self):
        api_key = os.environ.get("SERPAPI_KEY")
        if not api_key:
            raise EnvironmentError(
                "SERPAPI_KEY no configurada. "
                "Es la misma clave que se usa en el Flight Agent."
            )
        self._api_key = api_key
        self._base_url = "https://serpapi.com/search"

    # ------------------------------------------------------------------
    # Propiedades del plugin
    # ------------------------------------------------------------------

    @property
    def source_name(self) -> str:
        return "serpapi_events"

    @property
    def reliability_tier(self) -> SourceTier:
        # Google Events agrega datos de múltiples fuentes (Eventbrite, venue sites, etc.)
        # Tier SCRAPING porque no es una API de primera parte
        return SourceTier.SCRAPING

    @property
    def rate_limit_calls_per_minute(self) -> int:
        return 10

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

        seen_keys: set[str] = set()
        concerts: list[Concert] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for country in countries:
                config = COUNTRY_SEARCH_CONFIG.get(country)
                if not config:
                    continue

                for query in config["queries"]:
                    try:
                        raw_events = await self._search_events(
                            client,
                            query=query,
                            gl=config["gl"],
                            hl=config["hl"],
                        )

                        for event in raw_events:
                            concert = self._parse_event(event, country)
                            if not concert:
                                continue

                            # Filtro de fecha
                            if concert.event_date < from_date or concert.event_date > to_date:
                                continue

                            # Deduplicar
                            dedup_key = (
                                f"{_normalize(concert.band_name)}"
                                f"_{concert.event_date}"
                                f"_{country.value}"
                            )
                            if dedup_key not in seen_keys:
                                seen_keys.add(dedup_key)
                                concerts.append(concert)

                        # Pausa entre queries para respetar rate limit
                        await asyncio.sleep(1.2)

                    except Exception as e:
                        self.log_error(e, f"query: {query}")

        self.log_fetch_result(len(concerts))
        return concerts

    # ------------------------------------------------------------------
    # Llamada a SerpAPI
    # ------------------------------------------------------------------

    async def _search_events(
        self,
        client: httpx.AsyncClient,
        query: str,
        gl: str,
        hl: str,
    ) -> list[dict]:
        """Ejecuta una búsqueda de Google Events y retorna los resultados crudos."""
        try:
            resp = await client.get(
                self._base_url,
                params={
                    "engine": "google_events",
                    "q": query,
                    "gl": gl,
                    "hl": hl,
                    "api_key": self._api_key,
                },
            )
            resp.raise_for_status()
            return resp.json().get("events_results", [])
        except Exception as e:
            self.log_error(e, f"SerpAPI query: {query}")
            return []

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_event(self, event: dict, country: Country) -> Optional[Concert]:
        """Convierte un resultado de Google Events a nuestro modelo Concert."""
        try:
            title = event.get("title", "").strip()
            if not title:
                return None

            # Pre-filtro: descartar eventos claramente no relacionados con metal
            if not self._looks_like_metal(title, event.get("description", "")):
                return None

            # Fecha
            event_date = self._extract_date(event)
            if not event_date:
                return None

            # Venue y ciudad
            venue_info = event.get("venue", {})
            venue_name = venue_info.get("name", "")
            address_parts = event.get("address", [])
            # address es una lista: ["Venue Name", "City, Country"]
            city = ""
            if len(address_parts) >= 2:
                # "Bogotá, Colombia" → "Bogotá"
                city = address_parts[-1].split(",")[0].strip()
            elif len(address_parts) == 1:
                city = address_parts[0].split(",")[0].strip()

            ticket_url = event.get("link", "")

            # Usar el título completo como band_name.
            # El Orchestrator's LLM clasificará si es de metal y refinará el nombre.
            return Concert(
                band_name=title,
                event_date=event_date,
                city=city,
                country=country,
                source=self.source_name,
                source_tier=self.reliability_tier,
                event_id=f"serp_{abs(hash(title + str(event_date) + country.value))}",
                event_type=EventType.CONCERT,
                venue=venue_name,
                genres=[],
                ticket_url=ticket_url,
                confidence=0.75,  # Menor confianza que APIs oficiales
                raw_text=event.get("description", ""),
            )

        except Exception as e:
            self.log_error(e, "parse_event")
            return None

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _looks_like_metal(self, title: str, description: str) -> bool:
        """
        Pre-filtra resultados descartando eventos claramente no-metal.
        Combina el filtro genérico de la base class con keywords adicionales.
        """
        combined = (title + " " + description).lower()
        if any(kw in combined for kw in METAL_FILTER_KEYWORDS):
            return True
        return self.filter_by_genre_keywords(combined)

    def _extract_date(self, event: dict) -> Optional[date]:
        """
        Google Events devuelve fechas en formatos variados.
        Intentamos parsear en este orden:
          1. date.start_date  (puede ser "Apr 15, 2026" o "Apr 15" o ISO)
          2. date.when        ("Sat, Apr 15, 2026, 8:00 PM")
          3. Regex sobre cualquier campo de texto del evento
        """
        date_info = event.get("date", {})
        current_year = datetime.utcnow().year

        for raw in [date_info.get("start_date", ""), date_info.get("when", "")]:
            parsed = _try_parse_date(raw, current_year)
            if parsed:
                return parsed

        # Último recurso: buscar patrón YYYY-MM-DD en cualquier campo
        blob = str(event)
        match = re.search(r"\b(202[5-9]|203\d)-(\d{2})-(\d{2})\b", blob)
        if match:
            try:
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                pass

        return None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers de módulo (no métodos)
# ──────────────────────────────────────────────────────────────────────────────

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # Español
    "ene": 1, "abr": 4, "ago": 8, "dic": 12,
    # Portugués
    "fev": 2, "mai": 5, "set": 9, "out": 10, "dez": 12,
    # Finlandés (los meses en finlandés son distintos, fallback a inglés)
}

_DATE_FORMATS = [
    "%b %d, %Y",    # Apr 15, 2026
    "%B %d, %Y",    # April 15, 2026
    "%d %b %Y",     # 15 Apr 2026
    "%d/%m/%Y",     # 15/04/2026
    "%Y-%m-%d",     # 2026-04-15
    "%a, %b %d, %Y",  # Sat, Apr 15, 2026
]


def _try_parse_date(raw: str, current_year: int) -> Optional[date]:
    if not raw:
        return None

    # Limpiar: quitar hora y timezone
    cleaned = re.sub(r",?\s+\d{1,2}:\d{2}(\s*(AM|PM|UTC|GMT|[+-]\d+))?", "", raw).strip()

    # Intentar formatos directos
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    # "Apr 15" sin año → asumir próximo año si ya pasó
    match = re.match(r"([A-Za-z]{3})\s+(\d{1,2})$", cleaned)
    if match:
        month = _MONTH_ABBR.get(match.group(1).lower())
        day = int(match.group(2))
        if month:
            year = current_year
            try:
                candidate = date(year, month, day)
                if candidate < date.today():
                    candidate = date(year + 1, month, day)
                return candidate
            except ValueError:
                pass

    return None


def _normalize(text: str) -> str:
    """Normaliza un string para deduplicación."""
    return re.sub(r"\W+", "", text.lower())
