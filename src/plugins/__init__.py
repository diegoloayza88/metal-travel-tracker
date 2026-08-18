"""
plugins/__init__.py
--------------------
Central registry of concert source plugins.

Active plugins:
  - TicketmasterPlugin   -> Official API, covers all 11 monitored countries
  - SerpApiEventsPlugin  -> Google Events via SerpAPI, strongest for CO/CL/BR/MX
  - FestivalsPlugin      -> 7 hand-picked festival websites, official lineups

Disabled plugins (kept for reference):
  - BandsintownPlugin   -> Deprecated API / no useful results
  - EventbritePlugin    -> /v3/events/search/ endpoint discontinued for non-partner accounts
  - SongkickPlugin      -> No API key access
  - MetalArchivesPlugin -> Blocks AWS IPs with 403

To add a new plugin:
  1. Create a file in this directory implementing ConcertSourcePlugin
  2. Import it here and add it to `plugin_classes` in get_active_plugins()
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
