"""
plugins/__init__.py
--------------------
Registro central de plugins de fuentes de conciertos.

Plugins activos:
  - TicketmasterPlugin   → API oficial, cubre US/MX/ES/FI/BR/CO/CL
  - SerpApiEventsPlugin  → Google Events via SerpAPI, fuerte en CO/CL/BR

Plugins desactivados (mantenidos para referencia):
  - BandsintownPlugin   → API deprecada / sin resultados útiles
  - EventbritePlugin    → Endpoint /v3/events/search/ descontinuado para cuentas no-partner
  - SongkickPlugin      → Sin acceso a API key
  - MetalArchivesPlugin → Bloquea IPs de AWS con 403

Para agregar un nuevo plugin:
  1. Crea tu archivo en este directorio implementando ConcertSourcePlugin
  2. Impórtalo aquí y agrégalo a `plugin_classes` en get_active_plugins()
"""

from src.plugins.base import ConcertSourcePlugin
from src.plugins.festivals import FestivalsPlugin
from src.plugins.serpapi_events import SerpApiEventsPlugin
from src.plugins.ticketmaster import TicketmasterPlugin

# Plugins legacy (importados para que el resto del código no rompa si los referencia)
from src.plugins.bandsintown import BandsintownPlugin
from src.plugins.eventbrite import EventbritePlugin
from src.plugins.metal_archives import MetalArchivesPlugin
from src.plugins.songkick import SongkickPlugin

__all__ = [
    "ConcertSourcePlugin",
    "TicketmasterPlugin",
    "SerpApiEventsPlugin",
    "FestivalsPlugin",
    # legacy
    "BandsintownPlugin",
    "EventbritePlugin",
    "SongkickPlugin",
    "MetalArchivesPlugin",
    "get_active_plugins",
]


def get_active_plugins() -> list[ConcertSourcePlugin]:
    """
    Instancia y retorna todos los plugins habilitados.
    Los plugins que no tienen su API key configurada se saltan
    con un warning en lugar de fallar el proceso.
    """
    import logging

    logger = logging.getLogger(__name__)

    plugin_classes = [
        TicketmasterPlugin,  # API oficial: US, MX, ES, FI, BR, CO, CL
        SerpApiEventsPlugin,  # Google Events: todos los países (reutiliza SERPAPI_KEY)
        FestivalsPlugin,  # 9 festivales de referencia con lineup directo
        # BandsintownPlugin   → deprecado
        # EventbritePlugin    → endpoint descontinuado
        # SongkickPlugin      → sin API key
        # MetalArchivesPlugin → bloqueado por AWS IPs
    ]

    active = []
    for PluginClass in plugin_classes:
        try:
            plugin = PluginClass()
            if plugin.is_enabled:
                active.append(plugin)
                logger.info(
                    f"Plugin cargado: {plugin.source_name} (Tier {plugin.reliability_tier})"
                )
        except EnvironmentError as e:
            logger.warning(f"Plugin {PluginClass.__name__} no disponible: {e}")
        except Exception as e:
            logger.error(f"Error inicializando {PluginClass.__name__}: {e}")

    return active
