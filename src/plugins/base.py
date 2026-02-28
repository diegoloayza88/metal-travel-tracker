"""
plugins/base.py
---------------
Interfaz abstracta que todos los plugins de fuentes de conciertos deben implementar.

Para agregar una nueva fuente al sistema, simplemente crea un archivo nuevo en este
directorio e implementa esta clase base. El Concert Agent descubrirá y usará el plugin
automáticamente si lo registras en plugins/__init__.py.
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional
import logging

from src.models.concert import Concert, Country, MetalGenre, SourceTier

logger = logging.getLogger(__name__)


class ConcertSourcePlugin(ABC):
    """
    Interfaz base para todos los conectores de fuentes de conciertos.

    Ejemplo de implementación mínima:
    
        class MyPlugin(ConcertSourcePlugin):
            @property
            def source_name(self) -> str:
                return "my_source"

            @property
            def reliability_tier(self) -> SourceTier:
                return SourceTier.SCRAPING

            async def fetch_concerts(self, countries, genres, from_date, to_date):
                # Tu lógica aquí
                return []
    """

    # -------------------------------------------------------------------
    # Propiedades abstractas (obligatorias en cada plugin)
    # -------------------------------------------------------------------

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Nombre identificador de la fuente. Ej: 'songkick', 'metal_archives'."""
        pass

    @property
    @abstractmethod
    def reliability_tier(self) -> SourceTier:
        """Nivel de confiabilidad de esta fuente."""
        pass

    # -------------------------------------------------------------------
    # Método principal abstracto
    # -------------------------------------------------------------------

    @abstractmethod
    async def fetch_concerts(
        self,
        countries: list[Country],
        genres: list[MetalGenre],
        from_date: date,
        to_date: date,
    ) -> list[Concert]:
        """
        Busca conciertos en la fuente según los filtros dados.

        Args:
            countries:  Lista de países donde buscar (enum Country).
            genres:     Lista de géneros de metal de interés.
            from_date:  Fecha de inicio del rango de búsqueda.
            to_date:    Fecha de fin del rango de búsqueda.

        Returns:
            Lista de objetos Concert encontrados. Lista vacía si no hay resultados.
            Nunca debe lanzar excepción; maneja errores internamente y retorna [].
        """
        pass

    # -------------------------------------------------------------------
    # Propiedades opcionales con valores por defecto
    # -------------------------------------------------------------------

    @property
    def is_enabled(self) -> bool:
        """
        Permite deshabilitar un plugin sin eliminarlo.
        Override en el plugin si quieres control dinámico.
        """
        return True

    @property
    def rate_limit_calls_per_minute(self) -> Optional[int]:
        """
        Límite de llamadas por minuto a la fuente.
        None = sin límite conocido. El plugin debe respetar esto internamente.
        """
        return None

    @property
    def supported_countries(self) -> Optional[list[Country]]:
        """
        Países que soporta este plugin.
        None = soporta todos los países de interés.
        Override si el plugin solo cubre algunos países.
        """
        return None

    # -------------------------------------------------------------------
    # Métodos de utilidad disponibles para todos los plugins
    # -------------------------------------------------------------------

    def filter_by_genre_keywords(self, text: str) -> bool:
        """
        Utilidad: determina si un texto contiene keywords de géneros de metal.
        Útil para filtrar resultados de APIs que no tienen filtro de género.

        Args:
            text: Texto a analizar (nombre de banda, descripción, tags, etc.)

        Returns:
            True si el texto parece ser de metal relevante.
        """
        metal_keywords = {
            # Géneros directos
            "black metal", "death metal", "war metal", "heavy metal", "thrash metal",
            # Variantes comunes
            "blackmetal", "deathmetal", "thrash", "black/death", "death/black",
            # Subgéneros que queremos capturar
            "doom metal", "power metal", "speed metal", "grindcore",
            "brutal death", "technical death", "melodic death",
            "symphonic black", "raw black", "atmospheric black",
            # Términos generales de metal extremo
            "extreme metal", "metal extremo",
        }
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in metal_keywords)

    def log_fetch_start(self, countries: list[Country], from_date: date, to_date: date):
        """Log estandarizado al inicio de una búsqueda."""
        country_codes = [c.value for c in countries]
        logger.info(
            f"[{self.source_name}] Buscando conciertos | "
            f"Países: {country_codes} | "
            f"Rango: {from_date} → {to_date}"
        )

    def log_fetch_result(self, count: int):
        """Log estandarizado al finalizar una búsqueda."""
        logger.info(f"[{self.source_name}] Encontrados {count} conciertos")

    def log_error(self, error: Exception, context: str = ""):
        """Log estandarizado para errores."""
        logger.error(
            f"[{self.source_name}] Error{' en ' + context if context else ''}: "
            f"{type(error).__name__}: {error}"
        )
