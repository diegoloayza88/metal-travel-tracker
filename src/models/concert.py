"""
models/concert.py
-----------------
Modelos de datos centrales del proyecto.
Todos los agentes y plugins usan estas mismas clases para garantizar consistencia.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumeraciones
# ---------------------------------------------------------------------------

class MetalGenre(str, Enum):
    """Subgéneros de metal monitoreados."""
    BLACK_METAL   = "black_metal"
    DEATH_METAL   = "death_metal"
    WAR_METAL     = "war_metal"
    HEAVY_METAL   = "heavy_metal"
    THRASH_METAL  = "thrash_metal"
    UNKNOWN       = "unknown"


class EventType(str, Enum):
    """Tipo de evento musical."""
    CONCERT  = "concert"   # Concierto de una banda
    FESTIVAL = "festival"  # Festival con múltiples bandas
    UNKNOWN  = "unknown"


class Country(str, Enum):
    """Países de interés."""
    COLOMBIA       = "CO"
    CHILE          = "CL"
    BRAZIL         = "BR"
    UNITED_STATES  = "US"
    MEXICO         = "MX"
    FINLAND        = "FI"
    SPAIN          = "ES"


class SourceTier(int, Enum):
    """
    Nivel de confiabilidad de la fuente.
    Tier 1 = APIs oficiales (más confiable)
    Tier 2 = Scraping de sitios web
    Tier 3 = Fuentes informales (WhatsApp, redes sociales)
    """
    OFFICIAL = 1
    SCRAPING = 2
    INFORMAL = 3


class DealQuality(str, Enum):
    """Calidad de la oferta de vuelo comparada con el histórico."""
    EXCELLENT = "EXCELLENT"  # >30% por debajo del promedio
    GOOD      = "GOOD"       # 15-30% por debajo del promedio
    FAIR      = "FAIR"       # 5-15% por debajo del promedio
    NORMAL    = "NORMAL"     # Precio normal, sin descuento


# ---------------------------------------------------------------------------
# Modelos de Conciertos
# ---------------------------------------------------------------------------

@dataclass
class Concert:
    """
    Representa un concierto o festival de metal.
    Este es el modelo central que todos los plugins deben retornar.
    """
    # Campos obligatorios
    band_name:     str
    event_date:    date
    city:          str
    country:       Country
    source:        str           # Nombre de la fuente (ej: "songkick", "whatsapp_colombia")
    source_tier:   SourceTier

    # Campos opcionales
    event_id:      Optional[str]   = None   # ID único en la fuente original
    event_type:    EventType       = EventType.CONCERT
    venue:         Optional[str]   = None
    genres:        list[MetalGenre] = field(default_factory=list)
    ticket_url:    Optional[str]   = None
    ticket_price:  Optional[float] = None   # En USD, convertido si es necesario
    ticket_currency: Optional[str] = None
    festival_name: Optional[str]   = None   # Si es parte de un festival
    confidence:    float           = 1.0    # 0.0 a 1.0, relevante para fuentes Tier 3
    raw_text:      Optional[str]   = None   # Texto original del mensaje (para debug)
    created_at:    datetime        = field(default_factory=datetime.utcnow)

    def __post_init__(self):
        # Normalizar el nombre de la banda
        self.band_name = self.band_name.strip().title()

    @property
    def event_date_str(self) -> str:
        return self.event_date.strftime("%Y-%m-%d")

    @property
    def days_until_event(self) -> int:
        return (self.event_date - date.today()).days

    @property
    def unique_key(self) -> str:
        """Clave única para deduplicación en DynamoDB."""
        return f"{self.band_name.lower().replace(' ', '_')}_{self.event_date_str}_{self.country.value}"

    def to_dynamodb_item(self) -> dict:
        """Serializa para guardar en DynamoDB."""
        return {
            "pk":            {"S": f"CONCERT#{self.country.value}"},
            "sk":            {"S": f"{self.event_date_str}#{self.unique_key}"},
            "band_name":     {"S": self.band_name},
            "event_date":    {"S": self.event_date_str},
            "city":          {"S": self.city},
            "country":       {"S": self.country.value},
            "venue":         {"S": self.venue or "TBD"},
            "event_type":    {"S": self.event_type.value},
            "genres":        {"SS": [g.value for g in self.genres] if self.genres else ["unknown"]},
            "source":        {"S": self.source},
            "source_tier":   {"N": str(self.source_tier.value)},
            "ticket_url":    {"S": self.ticket_url or ""},
            "confidence":    {"N": str(self.confidence)},
            "created_at":    {"S": self.created_at.isoformat()},
        }


# ---------------------------------------------------------------------------
# Modelos de Vuelos
# ---------------------------------------------------------------------------

@dataclass
class Flight:
    """Representa una opción de vuelo encontrada."""
    origin:          str           # IATA code (ej: "LIM")
    destination:     str           # IATA code (ej: "BOG")
    departure_date:  date
    return_date:     Optional[date]
    price_usd:       float
    airline:         str
    booking_url:     str
    source:          str           # "amadeus" o "serpapi"

    # Análisis de precio
    price_avg_60d:   Optional[float] = None   # Promedio histórico 60 días
    price_p25_60d:   Optional[float] = None   # Percentil 25 histórico
    deal_quality:    DealQuality    = DealQuality.NORMAL
    discount_pct:    float          = 0.0     # % de descuento vs promedio

    # Metadata
    flight_duration_hours: Optional[float] = None
    stops:           int            = 0
    concert_ref:     Optional[str]  = None    # unique_key del concierto asociado
    found_at:        datetime       = field(default_factory=datetime.utcnow)

    @property
    def is_good_deal(self) -> bool:
        return self.deal_quality in (DealQuality.EXCELLENT, DealQuality.GOOD)

    def to_dynamodb_item(self) -> dict:
        return {
            "pk":              {"S": f"FLIGHT#{self.origin}#{self.destination}"},
            "sk":              {"S": f"{self.departure_date}#{self.found_at.isoformat()}"},
            "price_usd":       {"N": str(self.price_usd)},
            "airline":         {"S": self.airline},
            "booking_url":     {"S": self.booking_url},
            "deal_quality":    {"S": self.deal_quality.value},
            "discount_pct":    {"N": str(self.discount_pct)},
            "concert_ref":     {"S": self.concert_ref or ""},
            "stops":           {"N": str(self.stops)},
            "source":          {"S": self.source},
            "found_at":        {"S": self.found_at.isoformat()},
        }


# ---------------------------------------------------------------------------
# Modelos de Alojamiento
# ---------------------------------------------------------------------------

@dataclass
class Hotel:
    """Representa una opción de alojamiento."""
    name:           str
    city:           str
    country:        Country
    price_per_night_usd: float
    total_price_usd: float
    check_in:       date
    check_out:      date
    rating:         Optional[float] = None    # 0.0 a 10.0
    booking_url:    str             = ""
    distance_to_venue_km: Optional[float] = None
    concert_ref:    Optional[str]   = None
    found_at:       datetime        = field(default_factory=datetime.utcnow)

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


# ---------------------------------------------------------------------------
# Modelo de Deal Completo (vuelo + hotel + concierto)
# ---------------------------------------------------------------------------

@dataclass
class TravelDeal:
    """
    Agrupa un concierto con su mejor vuelo y hotel encontrados.
    Es lo que el Reporter Agent convierte en notificación.
    """
    concert:  Concert
    flight:   Optional[Flight] = None
    hotel:    Optional[Hotel]  = None

    @property
    def total_estimated_cost_usd(self) -> float:
        total = 0.0
        if self.flight:
            total += self.flight.price_usd
        if self.hotel:
            total += self.hotel.total_price_usd
        return total

    @property
    def is_notifiable(self) -> bool:
        """
        Determina si este deal merece una notificación.
        Notifica si: hay vuelo con buen precio O es un evento muy relevante.
        """
        if self.flight and self.flight.is_good_deal:
            return True
        # Siempre notifica conciertos en Finlandia (más raros y especiales)
        if self.concert.country == Country.FINLAND:
            return True
        return False
