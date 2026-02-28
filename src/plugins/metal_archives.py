"""
plugins/metal_archives.py
--------------------------
Plugin para scraping de la sección de shows de Encyclopaedia Metallum.
Metal-Archives es la base de datos más completa del metal underground mundial.
Su sección de shows (https://www.metal-archives.com/shows) lista conciertos
confirmados enviados por la comunidad.

Tier 2: Scraping — más frágil que APIs oficiales pero excelente cobertura
del metal underground que Songkick y Bandsintown no tienen.

IMPORTANTE: Respetar el rate limiting. Metal-Archives es un sitio comunitario
sin ánimo de lucro. Pausas de al menos 2 segundos entre requests.
"""

import asyncio
import re
from datetime import date, datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from src.models.concert import Concert, Country, EventType, MetalGenre, SourceTier
from src.plugins.base import ConcertSourcePlugin

# Metal-Archives usa nombres de países en inglés
COUNTRY_NAMES: dict[Country, list[str]] = {
    Country.COLOMBIA:      ["Colombia"],
    Country.CHILE:         ["Chile"],
    Country.BRAZIL:        ["Brazil"],
    Country.UNITED_STATES: ["United States"],
    Country.MEXICO:        ["Mexico"],
    Country.FINLAND:       ["Finland"],
    Country.SPAIN:         ["Spain"],
}

# Mapeo inverso: nombre de país → enum
NAME_TO_COUNTRY: dict[str, Country] = {
    name: country
    for country, names in COUNTRY_NAMES.items()
    for name in names
}

# Metal-Archives tiene una API no oficial para su sección de shows
# que devuelve datos en JSON (usada por su propio frontend)
MA_SHOWS_API = "https://www.metal-archives.com/events/ajax-list"


class MetalArchivesPlugin(ConcertSourcePlugin):
    """
    Scraper de la sección de eventos de Encyclopaedia Metallum (metal-archives.com).

    Usa la API interna AJAX del sitio que devuelve JSON, lo que es más
    robusto que scraping de HTML y menos probable de romperse con cambios
    de diseño del sitio.
    """

    def __init__(self):
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; MetalTravelTracker/1.0; "
                "Personal project for concert discovery)"
            ),
            "Accept":          "application/json, text/javascript, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer":         "https://www.metal-archives.com/",
            "X-Requested-With": "XMLHttpRequest",
        }

    @property
    def source_name(self) -> str:
        return "metal_archives"

    @property
    def reliability_tier(self) -> SourceTier:
        return SourceTier.SCRAPING

    @property
    def rate_limit_calls_per_minute(self) -> int:
        return 10  # Muy conservador para respetar el sitio comunitario

    async def fetch_concerts(
        self,
        countries: list[Country],
        genres: list[MetalGenre],
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        """
        Obtiene shows de Metal-Archives para los países de interés.
        Itera mes a mes porque la API está paginada por mes.
        """
        self.log_fetch_start(countries, from_date, to_date)
        concerts: list[Concert] = []

        # Generar lista de meses en el rango solicitado
        months = self._get_months_in_range(from_date, to_date)

        async with httpx.AsyncClient(
            timeout=30.0,
            headers=self._headers,
            follow_redirects=True,
        ) as client:
            for year, month in months:
                try:
                    month_concerts = await self._fetch_month(
                        client, year, month, countries
                    )
                    concerts.extend(month_concerts)
                    # Respetar rate limit — sitio comunitario
                    await asyncio.sleep(2.5)
                except Exception as e:
                    self.log_error(e, f"{year}-{month:02d}")

        self.log_fetch_result(len(concerts))
        return concerts

    async def _fetch_month(
        self,
        client: httpx.AsyncClient,
        year: int,
        month: int,
        target_countries: list[Country],
    ) -> list[Concert]:
        """Obtiene todos los shows de un mes específico."""
        concerts = []
        start = 0
        page_size = 100

        while True:
            try:
                response = await client.get(
                    MA_SHOWS_API,
                    params={
                        "sEcho":           1,
                        "iDisplayStart":   start,
                        "iDisplayLength":  page_size,
                        "sSortDir_0":      "asc",
                        "year":            year,
                        "month":           month,
                    },
                )
                response.raise_for_status()
                data = response.json()

                records = data.get("aaData", [])
                if not records:
                    break

                for record in records:
                    concert = self._parse_record(record, target_countries)
                    if concert:
                        concerts.append(concert)

                total = data.get("iTotalRecords", 0)
                start += page_size
                if start >= total:
                    break

                await asyncio.sleep(1.0)  # Pausa entre páginas

            except Exception as e:
                self.log_error(e, f"page {start}/{year}-{month:02d}")
                break

        return concerts

    def _parse_record(
        self,
        record: list,
        target_countries: list[Country],
    ) -> Optional[Concert]:
        """
        Parsea un registro de la API de Metal-Archives.

        El formato de cada registro es una lista con estos campos:
        [0] Fecha (HTML con link)
        [1] Bandas (HTML con links)
        [2] Venue (texto)
        [3] Ciudad (texto)
        [4] País (texto)
        [5] Tipo de evento (texto)
        [6] Descripción adicional
        """
        try:
            if len(record) < 5:
                return None

            # Extraer país
            country_str = self._strip_html(record[4]).strip()
            country = NAME_TO_COUNTRY.get(country_str)
            if not country or country not in target_countries:
                return None

            # Extraer fecha
            date_html = record[0]
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", date_html)
            if not date_match:
                return None
            event_date = date.fromisoformat(date_match.group(1))

            if event_date < date.today():
                return None

            # Extraer bandas (puede ser HTML con múltiples links)
            bands_html = record[1]
            bands = self._extract_band_names(bands_html)
            if not bands:
                return None

            # El headliner es la primera banda listada
            band_name = bands[0]

            # Venue y ciudad
            venue = self._strip_html(record[2]).strip() or "TBD"
            city  = self._strip_html(record[3]).strip() or country_str

            # Tipo de evento
            event_type_str = self._strip_html(record[5]).lower() if len(record) > 5 else ""
            event_type = (
                EventType.FESTIVAL
                if any(w in event_type_str for w in ["festival", "open air", "fest"])
                else EventType.CONCERT
            )

            return Concert(
                band_name=band_name,
                event_date=event_date,
                city=city,
                country=country,
                source=self.source_name,
                source_tier=self.reliability_tier,
                event_type=event_type,
                venue=venue,
                genres=[],  # Metal-Archives lista solo metal, géneros se clasifican luego
                confidence=0.9,  # Alta confianza — todo en MA es metal por definición
            )

        except Exception as e:
            self.log_error(e, "parse_record")
            return None

    @staticmethod
    def _strip_html(html: str) -> str:
        """Elimina tags HTML de un string."""
        return BeautifulSoup(html, "html.parser").get_text(separator=" ").strip()

    @staticmethod
    def _extract_band_names(html: str) -> list[str]:
        """Extrae nombres de bandas de HTML con múltiples links."""
        soup = BeautifulSoup(html, "html.parser")
        # Primero intentar extraer de links <a>
        links = soup.find_all("a")
        if links:
            return [a.get_text(strip=True) for a in links if a.get_text(strip=True)]
        # Fallback: texto plano separado por comas
        text = soup.get_text(separator=",")
        return [b.strip() for b in text.split(",") if b.strip()]

    @staticmethod
    def _get_months_in_range(from_date: date, to_date: date) -> list[tuple[int, int]]:
        """Genera lista de (año, mes) entre dos fechas."""
        months = []
        current = from_date.replace(day=1)
        end = to_date.replace(day=1)

        while current <= end:
            months.append((current.year, current.month))
            # Avanzar al siguiente mes
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        return months
