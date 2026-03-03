"""
plugins/__init__.py
--------------------
Registro central de plugins de fuentes de conciertos.

Para agregar un nuevo plugin al sistema:
  1. Crea tu archivo en este directorio implementando ConcertSourcePlugin
  2. Agrégalo a REGISTERED_PLUGINS con is_enabled=True

El Concert Agent importa REGISTERED_PLUGINS para descubrir las fuentes disponibles.
"""

from src.plugins.bandsintown import BandsintownPlugin
from src.plugins.base import ConcertSourcePlugin
from src.plugins.eventbrite import EventbritePlugin
from src.plugins.metal_archives import MetalArchivesPlugin
from src.plugins.songkick import SongkickPlugin

__all__ = [
    "ConcertSourcePlugin",
    "SongkickPlugin",
    "BandsintownPlugin",
    "EventbritePlugin",
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
        SongkickPlugin,
        BandsintownPlugin,
        EventbritePlugin,
        MetalArchivesPlugin,
    ]

    active = []
    for PluginClass in plugin_classes:
        try:
            plugin = PluginClass()
            if plugin.is_enabled:
                active.append(plugin)
                logger.info(f"Plugin cargado: {plugin.source_name} (Tier {plugin.reliability_tier})")
        except EnvironmentError as e:
            logger.warning(f"Plugin {PluginClass.__name__} no disponible: {e}")
        except Exception as e:
            logger.error(f"Error inicializando {PluginClass.__name__}: {e}")

    return active
