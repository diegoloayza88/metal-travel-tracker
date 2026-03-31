"""
plugins/festivals.py
--------------------
Plugin dedicado a los festivales de metal más importantes del circuito
underground y tradicional. A diferencia de Ticketmaster o SerpAPI Events,
este plugin conoce los festivales específicos que nos interesan y va
directamente a sus sitios web a buscar el lineup confirmado.

Festivales monitoreados:
  - Steelfest (Finlandia, junio)
  - Keep It True (Alemania, abril)
  - Inferno Metal Fest (Noruega, abril)
  - Under the Black Sun (Alemania, junio/julio)
  - Maryland Deathfest (EE.UU., mayo/junio)
  - Up the Hammers (Grecia, marzo)
  - Hell's Heroes (EE.UU., enero/febrero)
  - Underground for the Masses (Colombia)
  - Candelabrum Metal Fest (Colombia)

Estrategia:
  1. Hace HTTP GET al sitio oficial de cada festival.
  2. Extrae el texto de la página.
  3. Usa Bedrock LLM para parsear bands confirmadas + fechas + precio.
  4. Guarda el resultado en DynamoDB con TTL de 7 días (caché).
  5. Crea un objeto Concert por cada banda confirmada.

Variable de entorno requerida: ninguna adicional (usa DYNAMODB_TABLE_CONCERTS y AWS_REGION).
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

import boto3
import httpx

from src.models.concert import Concert, Country, EventType, MetalGenre, SourceTier
from src.plugins.base import ConcertSourcePlugin

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuración de festivales
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class FestivalConfig:
    """Configuración estática de un festival."""

    name: str
    country: Country
    city: str
    website: str
    typical_month: int  # Mes típico del festival (1-12)
    typical_year: int = 2026
    duration_days: int = 2
    ticket_url: str = ""
    approx_ticket_price_usd: int = 0  # 0 = desconocido
    lineup_path: str = ""  # Sub-ruta del lineup si es diferente a la raíz


TRACKED_FESTIVALS: list[FestivalConfig] = [
    FestivalConfig(
        name="Steelfest",
        country=Country.FINLAND,
        city="Hyvinkää",
        website="https://steelfest.fi",
        typical_month=6,
        duration_days=3,
        approx_ticket_price_usd=145,
        lineup_path="/bands",
    ),
    FestivalConfig(
        name="Keep It True",
        country=Country.GERMANY,
        city="Lauda-Königshofen",
        website="https://www.keep-it-true.de",
        typical_month=4,
        duration_days=2,
        approx_ticket_price_usd=85,
    ),
    FestivalConfig(
        name="Inferno Metal Fest",
        country=Country.NORWAY,
        city="Oslo",
        website="https://www.infernofestival.net",
        typical_month=4,
        duration_days=4,
        approx_ticket_price_usd=120,
        lineup_path="/bands",
    ),
    FestivalConfig(
        name="Under the Black Sun",
        country=Country.GERMANY,
        city="Berlín",
        website="https://www.under-the-black-sun.com",
        typical_month=7,
        duration_days=3,
        approx_ticket_price_usd=95,
    ),
    FestivalConfig(
        name="Maryland Deathfest",
        country=Country.UNITED_STATES,
        city="Baltimore",
        website="https://www.marylanddeathfest.com",
        typical_month=5,
        duration_days=4,
        approx_ticket_price_usd=130,
        lineup_path="/lineup",
    ),
    FestivalConfig(
        name="Up the Hammers",
        country=Country.GREECE,
        city="Atenas",
        website="https://www.upthehammers.gr",
        typical_month=3,
        duration_days=2,
        approx_ticket_price_usd=70,
    ),
    FestivalConfig(
        name="Hell's Heroes",
        country=Country.UNITED_STATES,
        city="Houston",
        website="https://hellsheroes.com",
        typical_month=1,
        typical_year=2027,  # Próxima edición será enero 2027
        duration_days=3,
        approx_ticket_price_usd=110,
    ),
    FestivalConfig(
        name="Underground for the Masses",
        country=Country.COLOMBIA,
        city="Bogotá",
        website="https://www.facebook.com/undergroundforthemasses",
        typical_month=8,
        duration_days=2,
        approx_ticket_price_usd=35,
    ),
    FestivalConfig(
        name="Candelabrum Metal Fest",
        country=Country.MEXICO,
        city="Ciudad de México",
        website="https://www.instagram.com/candelabrummetalfest",
        typical_month=9,
        duration_days=1,
        approx_ticket_price_usd=25,
    ),
]

# ──────────────────────────────────────────────────────────────────────────────
# Plugin
# ──────────────────────────────────────────────────────────────────────────────

_CACHE_TTL_DAYS = 7  # Refrescar lineup cada 7 días


class FestivalsPlugin(ConcertSourcePlugin):
    """
    Plugin de festivales de metal de referencia.

    Consulta directamente los sitios web de cada festival, extrae el lineup
    con ayuda del LLM y crea objetos Concert para cada banda confirmada.
    Los resultados se cachean en DynamoDB 7 días para evitar requests
    repetidos innecesarios.
    """

    def __init__(self):
        self._table_name = os.environ.get("DYNAMODB_TABLE_CONCERTS")
        self._region = os.environ.get("AWS_REGION", "us-east-1")

    @property
    def source_name(self) -> str:
        return "festivals"

    @property
    def reliability_tier(self) -> SourceTier:
        return SourceTier.SCRAPING

    @property
    def rate_limit_calls_per_minute(self) -> int:
        return 5  # Conservador: respetamos los sitios de festivales

    async def fetch_concerts(
        self,
        countries: list[Country],
        genres: list[MetalGenre],
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        self.log_fetch_start(countries, from_date, to_date)

        concerts: list[Concert] = []
        current_year = from_date.year

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            for festival in TRACKED_FESTIVALS:
                # Solo procesar festivales en el rango de fechas solicitado
                fest_year = festival.typical_year
                fest_month = festival.typical_month
                try:
                    approx_date = date(fest_year, fest_month, 15)
                except ValueError:
                    continue

                if not (from_date <= approx_date <= to_date):
                    continue

                try:
                    fest_concerts = await self._process_festival(
                        client, festival, current_year
                    )
                    concerts.extend(fest_concerts)
                    await asyncio.sleep(2.0)  # Respetar rate limit entre festivales
                except Exception as e:
                    self.log_error(e, f"festival: {festival.name}")

        self.log_fetch_result(len(concerts))
        return concerts

    # ── Procesamiento de un festival ───────────────────────────────────────

    async def _process_festival(
        self,
        client: httpx.AsyncClient,
        festival: FestivalConfig,
        current_year: int,
    ) -> list[Concert]:
        """
        Intenta obtener el lineup del festival:
        1. Revisa la caché en DynamoDB.
        2. Si no hay caché válida, hace HTTP GET al sitio.
        3. Extrae bandas con LLM.
        4. Guarda en caché y retorna los Concert objects.
        """
        # Verificar caché primero
        cached = self._load_cache(festival.name, festival.typical_year)
        if cached is not None:
            logger.info(
                f"[festivals] {festival.name}: usando caché " f"({len(cached)} bandas)"
            )
            return self._build_concerts(festival, cached)

        # Fetchear el sitio del festival
        html_text = await self._fetch_website(client, festival)
        if not html_text:
            # Facebook/Instagram bloquean AWS — esperado, no es un error
            social = (
                "facebook.com" in festival.website
                or "instagram.com" in festival.website
            )
            if social:
                logger.info(
                    f"[festivals] {festival.name}: sitio social ({festival.website}) "
                    "no accesible desde AWS — usando placeholder"
                )
            else:
                logger.warning(
                    f"[festivals] {festival.name}: no se pudo acceder al sitio"
                )
            return self._build_concerts(festival, [])

        # Extraer bandas con LLM
        bands = self._extract_bands_with_llm(html_text, festival)

        # Guardar en caché
        self._save_cache(festival.name, festival.typical_year, bands)

        logger.info(f"[festivals] {festival.name}: {len(bands)} bandas extraídas")
        return self._build_concerts(festival, bands)

    async def _fetch_website(
        self,
        client: httpx.AsyncClient,
        festival: FestivalConfig,
    ) -> Optional[str]:
        """Descarga el HTML del festival y retorna el texto limpio."""
        urls_to_try = [festival.website]
        if festival.lineup_path:
            urls_to_try.insert(0, festival.website.rstrip("/") + festival.lineup_path)

        for url in urls_to_try:
            try:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
                        )
                    },
                )
                if resp.status_code == 200:
                    return _strip_html(resp.text)[:8000]  # Max 8K chars para el LLM
            except Exception:
                continue

        return None

    def _extract_bands_with_llm(
        self,
        text: str,
        festival: FestivalConfig,
    ) -> list[str]:
        """
        Usa Bedrock para extraer nombres de bandas confirmadas del texto HTML.
        """
        try:
            # Import aquí para evitar dependencia circular en tests
            from src.shared.bedrock_client import BedrockClient

            bedrock = BedrockClient()

            prompt = f"""El siguiente texto proviene del sitio web oficial del festival "{festival.name}" ({festival.city}, {festival.country.value}).

TEXTO:
{text[:4000]}

Extrae SOLO los nombres de bandas de metal CONFIRMADAS para {festival.typical_year}.
Ignora:
- Años anteriores
- Patrocinadores
- Nombres de organizadores
- Palabras generales como "lineup", "bands", "artists"

Responde únicamente con JSON:
{{"confirmed_bands": ["Nombre Banda 1", "Nombre Banda 2", ...]}}

Si no hay bandas confirmadas o el lineup no está anunciado, responde:
{{"confirmed_bands": []}}"""

            response = bedrock.invoke(prompt, max_tokens=600, temperature=0.0)
            if not response or not response.strip():
                logger.warning(
                    f"[festivals] {festival.name}: Bedrock devolvió respuesta vacía"
                )
                return []
            # Extraer solo el objeto JSON aunque haya texto antes o después
            match = re.search(r"\{.*?\}", response, re.DOTALL)
            if not match:
                logger.warning(
                    f"[festivals] {festival.name}: no se encontró JSON en respuesta"
                )
                return []
            data = json.loads(match.group())
            bands = data.get("confirmed_bands", [])
            return [b.strip() for b in bands if isinstance(b, str) and len(b) >= 2]

        except Exception as e:
            logger.error(f"[festivals] LLM error para {festival.name}: {e}")
            return []

    def _build_concerts(
        self,
        festival: FestivalConfig,
        bands: list[str],
    ) -> list[Concert]:
        """
        Crea objetos Concert para cada banda del festival.
        Si no hay lineup, crea un Concert placeholder para que el festival
        aparezca en el reporte aunque no tenga bandas aún.
        """
        fest_date = date(
            festival.typical_year,
            festival.typical_month,
            15,  # Día aproximado hasta que se confirme
        )

        ticket_price_usd = (
            float(festival.approx_ticket_price_usd)
            if festival.approx_ticket_price_usd > 0
            else None
        )

        if not bands:
            # Festival anunciado pero sin lineup — crear entrada placeholder
            return [
                Concert(
                    band_name=f"[{festival.name} — Lineup por anunciar]",
                    event_date=fest_date,
                    city=festival.city,
                    country=festival.country,
                    source=self.source_name,
                    source_tier=self.reliability_tier,
                    event_id=f"festival_{festival.name.lower().replace(' ', '_')}_{festival.typical_year}",
                    event_type=EventType.FESTIVAL,
                    festival_name=festival.name,
                    ticket_url=festival.ticket_url or festival.website,
                    ticket_price=ticket_price_usd,
                    ticket_currency="USD",
                    genres=[MetalGenre.HEAVY_METAL],
                    confidence=0.9,
                    raw_text=f"Festival confirmado para {festival.typical_month}/{festival.typical_year}. Lineup pendiente.",
                )
            ]

        concerts = []
        for band in bands:
            concerts.append(
                Concert(
                    band_name=band,
                    event_date=fest_date,
                    city=festival.city,
                    country=festival.country,
                    source=self.source_name,
                    source_tier=self.reliability_tier,
                    event_id=f"festival_{festival.name.lower().replace(' ', '_')}_{festival.typical_year}_{band.lower().replace(' ', '_')[:20]}",
                    event_type=EventType.FESTIVAL,
                    festival_name=festival.name,
                    venue=festival.city,
                    ticket_url=festival.ticket_url or festival.website,
                    ticket_price=ticket_price_usd,
                    ticket_currency="USD",
                    genres=[],
                    confidence=0.95,
                )
            )
        return concerts

    # ── DynamoDB caché ─────────────────────────────────────────────────────

    def _load_cache(self, festival_name: str, year: int) -> Optional[list[str]]:
        """Retorna la lista de bandas si hay caché válida (< 7 días), None si no."""
        if not self._table_name:
            return None
        try:
            dynamodb = boto3.resource("dynamodb", region_name=self._region)
            table = dynamodb.Table(self._table_name)
            response = table.get_item(
                Key={
                    "pk": f"FESTIVAL_CACHE#{festival_name}",
                    "sk": str(year),
                }
            )
            item = response.get("Item")
            if not item:
                return None

            fetched_at = datetime.fromisoformat(item.get("fetched_at", "2000-01-01"))
            if datetime.utcnow() - fetched_at > timedelta(days=_CACHE_TTL_DAYS):
                return None  # Caché expirada

            bands = list(item.get("bands", []))
            return bands

        except Exception as e:
            logger.warning(f"[festivals] Error leyendo caché de {festival_name}: {e}")
            return None

    def _save_cache(self, festival_name: str, year: int, bands: list[str]) -> None:
        """Guarda el lineup en DynamoDB con TTL de 30 días."""
        if not self._table_name:
            return
        try:
            dynamodb = boto3.resource("dynamodb", region_name=self._region)
            table = dynamodb.Table(self._table_name)
            ttl = int((datetime.utcnow() + timedelta(days=30)).timestamp())
            table.put_item(
                Item={
                    "pk": f"FESTIVAL_CACHE#{festival_name}",
                    "sk": str(year),
                    "bands": bands,
                    "fetched_at": datetime.utcnow().isoformat(),
                    "ttl": ttl,
                }
            )
        except Exception as e:
            logger.warning(f"[festivals] Error guardando caché de {festival_name}: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Utilidad: strip HTML
# ──────────────────────────────────────────────────────────────────────────────


def _strip_html(html: str) -> str:
    """Extrae texto limpio de HTML, eliminando scripts, estilos y tags."""
    # Eliminar scripts y estilos
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.I)
    # Eliminar tags HTML
    html = re.sub(r"<[^>]+>", " ", html)
    # Decodificar entidades básicas
    html = html.replace("&amp;", "&").replace("&nbsp;", " ").replace("&lt;", "<")
    # Colapsar espacios
    html = re.sub(r"\s+", " ", html).strip()
    return html
