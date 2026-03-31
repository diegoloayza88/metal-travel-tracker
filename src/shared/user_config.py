"""
shared/user_config.py
---------------------
Preferencias personales del usuario: watchlist de bandas, aeropuerto de origen
y parámetros de viaje.

Se almacena en DynamoDB bajo pk="CONFIG#USER", sk="preferences" (misma tabla
de conciertos). Todos los agentes que necesitan personalización la cargan una
sola vez al inicio.

Uso:
    prefs = load_user_preferences()
    if prefs.is_watchlist_match("Mgła"):
        ...
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import boto3

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Watchlist inicial — bandas más importantes para el usuario
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_WATCHLIST: list[str] = [
    # Black Metal nórdico / underground
    "Mgła",
    "Bewitched",
    "Beherit",
    "Horna",
    "Sargeist",
    "Clandestine Blaze",
    "Forteresse",
    "Warmoon Lord",
    "Emperor",
    "Tormentor",
    "Akhlys",
    # Black Metal europeo / guerra
    "Baxaxaxa",
    "Grand Belial's Key",
    "Negative Plane",
    "Spirit Possession",
    # War Metal / bestial
    "Diocletian",
    "Revenge",
    "Invunche",
    # Death Metal
    "Disma",
    "Blood Incantation",
    "Vastum",
    "Mortiferum",
    "Ulthar",
    "Spectral Voice",
    "Messiah",
    # Thrash / Clásicos
    "Slayer",
]

# ──────────────────────────────────────────────────────────────────────────────
# Estimaciones de vuelo LIM → ciudad (en USD, rango min-max)
# Se usan para presupuestos aproximados cuando Amadeus no tiene datos.
# ──────────────────────────────────────────────────────────────────────────────

FLIGHT_ESTIMATE_USD: dict[str, tuple[int, int]] = {
    # Sudamérica / región
    "PE": (0, 0),  # Lima mismo
    "CO": (180, 380),  # LIM → BOG
    "CL": (120, 280),  # LIM → SCL
    "BR": (420, 750),  # LIM → GRU/GIG
    "MX": (380, 720),  # LIM → MEX
    # Norteamérica
    "US": (450, 950),  # LIM → JFK/LAX/IAH/BWI
    # Europa occidental
    "ES": (720, 1250),  # LIM → MAD
    "DE": (820, 1380),  # LIM → FRA/MUC
    "FI": (950, 1550),  # LIM → HEL
    "NO": (950, 1600),  # LIM → OSL
    "GR": (1050, 1700),  # LIM → ATH
    "RO": (900, 1450),  # LIM → OTP (Bucarest)
}

# Estimación de hotel por noche (USD)
HOTEL_ESTIMATE_USD: dict[str, tuple[int, int]] = {
    "PE": (40, 80),
    "CO": (45, 90),
    "CL": (60, 110),
    "BR": (55, 100),
    "MX": (50, 95),
    "US": (90, 180),
    "ES": (75, 140),
    "DE": (80, 150),
    "FI": (90, 160),
    "NO": (100, 175),
    "GR": (65, 120),
    "RO": (50, 95),  # Bucarest
}

# Mejor momento para comprar según tipo (semanas antes del evento)
BUY_WINDOW_FLIGHTS: dict[str, str] = {
    "PE": "2-4 semanas antes",
    "CO": "3-6 semanas antes",
    "CL": "3-6 semanas antes",
    "BR": "6-10 semanas antes",
    "MX": "6-10 semanas antes",
    "US": "8-14 semanas antes",
    "ES": "10-16 semanas antes",
    "DE": "10-16 semanas antes",
    "FI": "12-18 semanas antes",
    "NO": "12-18 semanas antes",
    "GR": "12-18 semanas antes",
    "RO": "10-16 semanas antes",
}


# ──────────────────────────────────────────────────────────────────────────────
# Modelo
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class UserPreferences:
    """Preferencias personales que personalizan el comportamiento de todo el sistema."""

    watchlist_bands: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))
    home_airport: str = "LIM"
    home_city: str = "Lima"
    home_country: str = "PE"
    budget_flight_max_usd: int = 2000
    budget_trip_max_usd: int = 3500
    preferred_genres: list[str] = field(
        default_factory=lambda: ["black_metal", "death_metal", "war_metal"]
    )

    # ── Watchlist matching ──────────────────────────────────────────────

    @property
    def _watchlist_lower(self) -> list[str]:
        return [b.lower().strip() for b in self.watchlist_bands]

    def is_watchlist_match(self, band_name: str) -> bool:
        """True si la banda está (exacta o parcialmente) en la watchlist."""
        band_lower = band_name.lower().strip()
        for watched in self._watchlist_lower:
            if watched == band_lower:
                return True
            # Captura variantes: "Mgla" == "Mgła", "Messiah" dentro de "Messiah (Suiza)"
            if len(watched) >= 4 and (watched in band_lower or band_lower in watched):
                return True
        return False

    def watchlist_score(self, band_name: str) -> float:
        """
        0.0  → no está en watchlist
        8.0  → match parcial
        10.0 → match exacto
        """
        band_lower = band_name.lower().strip()
        for watched in self._watchlist_lower:
            if watched == band_lower:
                return 10.0
            if len(watched) >= 4 and (watched in band_lower or band_lower in watched):
                return 8.0
        return 0.0

    # ── Estimaciones de presupuesto ─────────────────────────────────────

    def estimate_flight_usd(self, country_code: str) -> Optional[tuple[int, int]]:
        """Retorna (min_usd, max_usd) estimado para vuelo LIM → país."""
        return FLIGHT_ESTIMATE_USD.get(country_code.upper())

    def estimate_hotel_usd(
        self, country_code: str, nights: int = 3
    ) -> Optional[tuple[int, int]]:
        """Retorna (min_usd, max_usd) estimado para hotel × N noches."""
        base = HOTEL_ESTIMATE_USD.get(country_code.upper())
        if not base:
            return None
        return (base[0] * nights, base[1] * nights)

    def estimate_total_trip_usd(
        self,
        country_code: str,
        nights: int = 3,
        ticket_usd: int = 0,
    ) -> tuple[int, int]:
        """Presupuesto total estimado: vuelo + hotel + entrada."""
        flight = FLIGHT_ESTIMATE_USD.get(country_code.upper(), (500, 1000))
        hotel = HOTEL_ESTIMATE_USD.get(country_code.upper(), (80, 150))
        total_min = flight[0] + hotel[0] * nights + ticket_usd
        total_max = flight[1] + hotel[1] * nights + ticket_usd
        return (total_min, total_max)

    def buy_window_flights(self, country_code: str) -> str:
        return BUY_WINDOW_FLIGHTS.get(country_code.upper(), "8-12 semanas antes")


# ──────────────────────────────────────────────────────────────────────────────
# DynamoDB: cargar / guardar
# ──────────────────────────────────────────────────────────────────────────────

_PREFS_PK = "CONFIG#USER"
_PREFS_SK = "preferences"


def load_user_preferences() -> UserPreferences:
    """
    Carga las preferencias del usuario desde DynamoDB.
    Si no existen, retorna los valores por defecto (incluye watchlist hardcodeada).
    """
    table_name = os.environ.get("DYNAMODB_TABLE_CONCERTS")
    if not table_name:
        return UserPreferences()

    try:
        dynamodb = boto3.resource(
            "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
        table = dynamodb.Table(table_name)
        response = table.get_item(Key={"pk": _PREFS_PK, "sk": _PREFS_SK})
        item = response.get("Item")

        if not item:
            logger.info(
                "Sin preferencias en DynamoDB — usando defaults (watchlist hardcodeada)"
            )
            return UserPreferences()

        return UserPreferences(
            watchlist_bands=list(item.get("watchlist_bands", DEFAULT_WATCHLIST)),
            home_airport=item.get("home_airport", "LIM"),
            home_city=item.get("home_city", "Lima"),
            home_country=item.get("home_country", "PE"),
            budget_flight_max_usd=int(item.get("budget_flight_max_usd", 2000)),
            budget_trip_max_usd=int(item.get("budget_trip_max_usd", 3500)),
            preferred_genres=list(
                item.get(
                    "preferred_genres", ["black_metal", "death_metal", "war_metal"]
                )
            ),
        )

    except Exception as e:
        logger.error(f"Error cargando preferencias: {e}")
        return UserPreferences()


def save_user_preferences(prefs: UserPreferences) -> bool:
    """Guarda preferencias en DynamoDB."""
    table_name = os.environ.get("DYNAMODB_TABLE_CONCERTS")
    if not table_name:
        return False

    try:
        dynamodb = boto3.resource(
            "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
        table = dynamodb.Table(table_name)
        table.put_item(
            Item={
                "pk": _PREFS_PK,
                "sk": _PREFS_SK,
                "watchlist_bands": prefs.watchlist_bands,
                "home_airport": prefs.home_airport,
                "home_city": prefs.home_city,
                "home_country": prefs.home_country,
                "budget_flight_max_usd": prefs.budget_flight_max_usd,
                "budget_trip_max_usd": prefs.budget_trip_max_usd,
                "preferred_genres": prefs.preferred_genres,
            }
        )
        logger.info(
            f"Preferencias guardadas: {len(prefs.watchlist_bands)} bandas en watchlist"
        )
        return True

    except Exception as e:
        logger.error(f"Error guardando preferencias: {e}")
        return False
