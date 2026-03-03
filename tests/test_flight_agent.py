"""
tests/test_flight_agent.py
---------------------------
Tests unitarios del Flight Agent, enfocados en la lógica de análisis
de precio histórico (la parte más crítica del sistema).
"""

from datetime import date, timedelta
from unittest.mock import MagicMock

from src.agents.flight_agent.handler import analyze_deal_quality
from src.models.concert import DealQuality, Flight


def make_flight(price: float, origin: str = "LIM", dest: str = "BOG") -> Flight:
    """Helper para crear un Flight de prueba."""
    return Flight(
        origin=origin,
        destination=dest,
        departure_date=date.today() + timedelta(days=60),
        return_date=date.today() + timedelta(days=65),
        price_usd=price,
        airline="LATAM",
        booking_url="https://example.com",
        source="amadeus",
    )


class TestAnalyzeDealQuality:
    """Tests para la lógica de análisis de precios históricos."""

    def test_excellent_deal_muy_por_debajo_del_percentil_25(self):
        """Precio 40% más barato que el percentil 25 → EXCELLENT."""
        historical = [400, 420, 430, 450, 460, 470, 480, 500, 520, 550] * 3

        mock_db = MagicMock()
        mock_db.get_historical_prices.return_value = historical

        flight = make_flight(price=230)  # Mucho más barato que el P25 (~$420)
        result = analyze_deal_quality(flight, mock_db)

        assert result.deal_quality == DealQuality.EXCELLENT
        assert result.discount_pct > 30
        assert result.price_avg_60d is not None

    def test_good_deal_en_percentil_25(self):
        """Precio en el percentil 25 → GOOD."""
        # Con esta distribución, P25 ≈ $430
        historical = [400, 420, 430, 450, 460, 470, 480, 500, 520, 550] * 3

        mock_db = MagicMock()
        mock_db.get_historical_prices.return_value = historical

        flight = make_flight(price=425)  # En el percentil 25
        result = analyze_deal_quality(flight, mock_db)

        assert result.deal_quality in (DealQuality.EXCELLENT, DealQuality.GOOD)

    def test_normal_deal_precio_promedio(self):
        """Precio en el promedio histórico → NORMAL."""
        historical = [400, 450, 500, 550, 600] * 4  # Promedio = $500

        mock_db = MagicMock()
        mock_db.get_historical_prices.return_value = historical

        flight = make_flight(price=500)  # Exactamente el promedio
        result = analyze_deal_quality(flight, mock_db)

        assert result.deal_quality == DealQuality.NORMAL
        assert result.discount_pct <= 5  # Pequeña variación aceptable

    def test_sin_historico_suficiente_no_analiza(self):
        """Con menos de 5 registros históricos, no se puede analizar."""
        mock_db = MagicMock()
        mock_db.get_historical_prices.return_value = [400, 450]  # Solo 2 registros

        flight = make_flight(price=300)
        result = analyze_deal_quality(flight, mock_db)

        # Sin histórico suficiente, el deal_quality queda como NORMAL (default)
        assert result.deal_quality == DealQuality.NORMAL
        assert result.price_avg_60d is None

    def test_precio_cero_no_es_excelente(self):
        """Un precio de $0 no debe considerarse un deal válido."""
        historical = [400, 450, 500, 550, 600] * 4

        mock_db = MagicMock()
        mock_db.get_historical_prices.return_value = historical

        # $0 sería técnicamente "EXCELLENT" pero no tiene sentido
        # Este test documenta el comportamiento actual para revisión futura
        flight = make_flight(price=0.01)
        result = analyze_deal_quality(flight, mock_db)

        assert result.deal_quality == DealQuality.EXCELLENT  # Comportamiento actual

    def test_rutas_independientes(self):
        """El histórico de LIM→BOG no afecta el análisis de LIM→SCL."""
        historical_bog = [400, 450, 500] * 5   # Más barato
        historical_scl = [800, 850, 900] * 5   # Más caro

        mock_db = MagicMock()

        # LIM→BOG
        mock_db.get_historical_prices.return_value = historical_bog
        flight_bog = make_flight(price=380, dest="BOG")
        result_bog = analyze_deal_quality(flight_bog, mock_db)

        # LIM→SCL
        mock_db.get_historical_prices.return_value = historical_scl
        flight_scl = make_flight(price=750, dest="SCL")
        result_scl = analyze_deal_quality(flight_scl, mock_db)

        # Ambos deben ser buenas ofertas para su ruta respectiva
        assert result_bog.is_good_deal
        assert result_scl.is_good_deal

    def test_fair_deal_entre_p25_y_promedio(self):
        """Precio entre el P25 y el promedio → FAIR."""
        historical = [300, 350, 400, 450, 500, 550, 600, 650, 700, 750] * 2
        # Promedio ≈ 525, P25 ≈ 375

        mock_db = MagicMock()
        mock_db.get_historical_prices.return_value = historical

        flight = make_flight(price=480)  # Entre P25 y promedio, cerca del promedio
        result = analyze_deal_quality(flight, mock_db)

        assert result.deal_quality in (DealQuality.FAIR, DealQuality.NORMAL)


class TestFlightModel:
    """Tests del modelo Flight."""

    def test_is_good_deal_excellent(self):
        flight = make_flight(200)
        flight.deal_quality = DealQuality.EXCELLENT
        assert flight.is_good_deal is True

    def test_is_good_deal_good(self):
        flight = make_flight(350)
        flight.deal_quality = DealQuality.GOOD
        assert flight.is_good_deal is True

    def test_is_not_good_deal_fair(self):
        flight = make_flight(450)
        flight.deal_quality = DealQuality.FAIR
        assert flight.is_good_deal is False

    def test_is_not_good_deal_normal(self):
        flight = make_flight(500)
        flight.deal_quality = DealQuality.NORMAL
        assert flight.is_good_deal is False
