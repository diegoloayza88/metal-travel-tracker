"""
tests/test_models.py
---------------------
Tests unitarios de los modelos de datos principales.
Verifica serialización a DynamoDB, unique_key y lógica de negocio.
"""

from datetime import date, timedelta

from src.models.concert import (
    Concert,
    Country,
    DealQuality,
    Flight,
    MetalGenre,
    SourceTier,
    TravelDeal,
)


class TestConcertModel:
    """Tests del modelo Concert."""

    def make_concert(self, **kwargs) -> Concert:
        """Helper para crear un concierto de prueba."""
        defaults = dict(
            band_name="Watain",
            event_date=date(2026, 8, 15),
            city="Bogotá",
            country=Country.COLOMBIA,
            source="songkick",
            source_tier=SourceTier.OFFICIAL,
        )
        defaults.update(kwargs)
        return Concert(**defaults)

    def test_band_name_normalizado(self):
        """El nombre de la banda se normaliza con title case."""
        concert = self.make_concert(band_name="  watain  ")
        assert concert.band_name == "Watain"

    def test_unique_key_consistente(self):
        """El unique_key debe ser determinístico para el mismo concierto."""
        c1 = self.make_concert()
        c2 = self.make_concert()
        assert c1.unique_key == c2.unique_key

    def test_unique_key_diferente_por_pais(self):
        """Misma banda, misma fecha, diferente país → unique_key diferente."""
        colombia = self.make_concert(country=Country.COLOMBIA)
        chile    = self.make_concert(country=Country.CHILE)
        assert colombia.unique_key != chile.unique_key

    def test_days_until_event(self):
        """Calcula correctamente los días hasta el evento."""
        future_date = date.today() + timedelta(days=30)
        concert = self.make_concert(event_date=future_date)
        assert abs(concert.days_until_event - 30) <= 1  # Tolerancia de 1 día

    def test_event_date_str_formato(self):
        """La fecha se formatea correctamente como string."""
        concert = self.make_concert(event_date=date(2026, 3, 15))
        assert concert.event_date_str == "2026-03-15"

    def test_to_dynamodb_item_contiene_campos_requeridos(self):
        """La serialización a DynamoDB incluye todos los campos necesarios."""
        concert = self.make_concert(
            genres=[MetalGenre.BLACK_METAL, MetalGenre.WAR_METAL]
        )
        item = concert.to_dynamodb_item()

        assert "pk" in item
        assert "sk" in item
        assert "band_name" in item
        assert "event_date" in item
        assert "country" in item
        assert "source" in item
        assert "source_tier" in item

    def test_to_dynamodb_item_pk_formato(self):
        """El partition key debe tener el formato CONCERT#<COUNTRY>."""
        concert = self.make_concert(country=Country.FINLAND)
        item = concert.to_dynamodb_item()
        assert item["pk"]["S"] == "CONCERT#FI"

    def test_confidence_default(self):
        """La confianza por defecto es 1.0 para fuentes oficiales."""
        concert = self.make_concert()
        assert concert.confidence == 1.0

    def test_confidence_personalizada(self):
        """Se puede establecer confianza personalizada (útil para WhatsApp)."""
        concert = self.make_concert(confidence=0.85)
        assert concert.confidence == 0.85


class TestTravelDealModel:
    """Tests del modelo TravelDeal."""

    def make_deal(self, flight_price=None, hotel_price=None) -> TravelDeal:
        concert = Concert(
            band_name="Kreator",
            event_date=date(2026, 9, 20),
            city="Santiago",
            country=Country.CHILE,
            source="bandsintown",
            source_tier=SourceTier.OFFICIAL,
        )

        flight = None
        if flight_price:
            flight = Flight(
                origin="LIM", destination="SCL",
                departure_date=date(2026, 9, 18),
                return_date=date(2026, 9, 22),
                price_usd=flight_price,
                airline="LATAM",
                booking_url="https://example.com",
                source="amadeus",
                deal_quality=DealQuality.GOOD,
            )

        from src.models.concert import Hotel
        hotel = None
        if hotel_price:
            hotel = Hotel(
                name="Hotel Metal Santiago",
                city="Santiago",
                country=Country.CHILE,
                price_per_night_usd=hotel_price,
                total_price_usd=hotel_price * 4,
                check_in=date(2026, 9, 18),
                check_out=date(2026, 9, 22),
            )

        return TravelDeal(concert=concert, flight=flight, hotel=hotel)

    def test_costo_total_vuelo_mas_hotel(self):
        """El costo total suma correctamente vuelo y hotel."""
        deal = self.make_deal(flight_price=400, hotel_price=60)
        # Hotel por 4 noches = $240, vuelo = $400, total = $640
        assert deal.total_estimated_cost_usd == 640.0

    def test_costo_total_solo_vuelo(self):
        """Costo total cuando solo hay vuelo (sin hotel)."""
        deal = self.make_deal(flight_price=350)
        assert deal.total_estimated_cost_usd == 350.0

    def test_costo_total_sin_nada(self):
        """Costo total es 0 si no hay vuelo ni hotel."""
        deal = self.make_deal()
        assert deal.total_estimated_cost_usd == 0.0

    def test_is_notifiable_con_buen_vuelo(self):
        """Un deal con buen vuelo siempre es notificable."""
        deal = self.make_deal(flight_price=300)
        assert deal.is_notifiable is True

    def test_is_notifiable_finlandia_siempre(self):
        """Conciertos en Finlandia son siempre notificables (raros y especiales)."""
        concert = Concert(
            band_name="Nightwish",
            event_date=date(2026, 7, 10),
            city="Helsinki",
            country=Country.FINLAND,
            source="songkick",
            source_tier=SourceTier.OFFICIAL,
        )
        deal = TravelDeal(concert=concert)  # Sin vuelo
        assert deal.is_notifiable is True

    def test_not_notifiable_sin_deal_ni_finlandia(self):
        """Sin buen vuelo y sin ser Finlandia, no es notificable."""
        deal = self.make_deal()  # Sin vuelo
        # Colombia sin vuelo no es notificable
        assert deal.is_notifiable is False
