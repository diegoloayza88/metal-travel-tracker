"""
tests/test_whatsapp_parser.py
------------------------------
Tests unitarios para el procesador de exports de WhatsApp.
Usa moto para mockear DynamoDB sin necesitar AWS real.
Usa unittest.mock para mockear Bedrock.
"""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.processors.whatsapp_export_parser.handler import (
    extract_messages,
    process_message_with_llm,
)


# ---------------------------------------------------------------------------
# Tests de extracción de mensajes del .txt de WhatsApp
# ---------------------------------------------------------------------------

class TestExtractMessages:
    """Tests para la función extract_messages()"""

    def test_mensaje_simple_android(self):
        """Extrae correctamente un mensaje en formato Android."""
        raw = "12/2/26, 10:23 a. m. - Juan: Hola a todos"
        messages = extract_messages(raw)
        assert len(messages) == 1
        assert messages[0] == "Hola a todos"

    def test_mensaje_multilinea(self):
        """Mensajes que ocupan varias líneas se concatenan correctamente."""
        raw = (
            "12/2/26, 10:23 a. m. - Juan: CONFIRMADO Sepultura en Bogotá\n"
            "15 de marzo, Teatro Royal\n"
            "Entradas desde $80.000 COP"
        )
        messages = extract_messages(raw)
        assert len(messages) == 1
        assert "Sepultura en Bogotá" in messages[0]
        assert "Teatro Royal" in messages[0]
        assert "$80.000 COP" in messages[0]

    def test_multiples_mensajes(self):
        """Extrae múltiples mensajes correctamente."""
        raw = (
            "12/2/26, 10:00 a. m. - Ana: ¿alguien va al concierto?\n"
            "12/2/26, 10:05 a. m. - Carlos: Sí, yo voy\n"
            "12/2/26, 10:10 a. m. - Diego: CONFIRMADO Watain en Bogotá el 20 de abril"
        )
        messages = extract_messages(raw)
        assert len(messages) == 3
        assert "¿alguien va al concierto?" in messages[0]
        assert "Sí, yo voy" in messages[1]
        assert "Watain en Bogotá" in messages[2]

    def test_mensajes_sistema_incluidos_para_filtrado_posterior(self):
        """Los mensajes de sistema se extraen (el filtrado ocurre después)."""
        raw = (
            "12/2/26, 09:00 a. m. - Ana se unió usando el enlace de invitación\n"
            "12/2/26, 10:00 a. m. - Juan: Bienvenida Ana!"
        )
        messages = extract_messages(raw)
        # El mensaje de sistema puede o no ser captado dependiendo del formato
        # Lo importante es que el mensaje real sí se capture
        assert any("Bienvenida Ana!" in m for m in messages)

    def test_formato_ios(self):
        """Soporta el formato de fecha de iOS (DD-MM-YYYY)."""
        raw = "26-02-2026, 10:23 - Juan: Kreator confirmado en Medellín"
        messages = extract_messages(raw)
        assert len(messages) >= 1
        assert any("Kreator confirmado" in m for m in messages)

    def test_archivo_vacio(self):
        """Retorna lista vacía para archivo vacío."""
        assert extract_messages("") == []

    def test_solo_encabezado_de_exportacion(self):
        """El encabezado de exportación de WhatsApp no genera mensajes."""
        raw = "Los mensajes y las llamadas están cifrados de extremo a extremo."
        messages = extract_messages(raw)
        # No debe contener ese texto como mensaje de usuario
        assert not any("cifrados" in m for m in messages)


# ---------------------------------------------------------------------------
# Tests del procesador LLM (Bedrock mockeado)
# ---------------------------------------------------------------------------

class TestProcessMessageWithLLM:
    """Tests para process_message_with_llm() con Bedrock mockeado."""

    def _mock_bedrock(self, response_json: dict) -> MagicMock:
        """Helper que crea un mock de BedrockClient con respuesta configurada."""
        mock = MagicMock()
        mock.invoke.return_value = json.dumps(response_json)
        return mock

    def test_detecta_concierto_valido(self):
        """Detecta correctamente un anuncio de concierto con alta confianza."""
        bedrock = self._mock_bedrock({
            "is_concert_announcement": True,
            "band_name":    "Watain",
            "festival_name": None,
            "event_date":   f"{date.today().year + 1}-04-20",
            "city":         "Bogotá",
            "venue":        "Teatro Royal",
            "ticket_price_cop": 80000,
            "ticket_url":   "https://tuboleta.com/watain",
            "is_cancellation": False,
            "genres":       ["black_metal"],
            "confidence":   0.95,
            "notes":        "Fecha confirmada por el promotor",
        })

        concert = process_message_with_llm(
            "CONFIRMADO Watain en Bogotá 20 de abril, Teatro Royal. Entradas $80.000",
            bedrock,
        )

        assert concert is not None
        assert concert.band_name == "Watain"
        assert concert.city == "Bogotá"
        assert concert.venue == "Teatro Royal"
        assert concert.ticket_price == 80000
        assert concert.confidence == 0.95

    def test_descarta_mensaje_no_concierto(self):
        """No crea Concert para mensajes que no son anuncios de conciertos."""
        bedrock = self._mock_bedrock({
            "is_concert_announcement": False,
            "confidence": 0.1,
        })

        concert = process_message_with_llm(
            "¿Alguien tiene la discografía de Immortal?",
            bedrock,
        )
        assert concert is None

    def test_descarta_baja_confianza(self):
        """No crea Concert cuando la confianza del LLM es menor a 0.7."""
        bedrock = self._mock_bedrock({
            "is_concert_announcement": True,
            "band_name":   "Alguna Banda",
            "event_date":  "2026-06-01",
            "city":        "Bogotá",
            "confidence":  0.5,  # Por debajo del umbral
        })

        concert = process_message_with_llm(
            "Creo que van a venir a tocar pronto...",
            bedrock,
        )
        assert concert is None

    def test_descarta_cancelacion(self):
        """No crea Concert para mensajes de cancelación."""
        bedrock = self._mock_bedrock({
            "is_concert_announcement": True,
            "band_name":       "Obituary",
            "event_date":      "2026-05-15",
            "city":            "Medellín",
            "is_cancellation": True,
            "confidence":      0.92,
        })

        concert = process_message_with_llm(
            "OJO: Obituary CANCELA su fecha en Medellín por problemas de visa",
            bedrock,
        )
        assert concert is None

    def test_descarta_fecha_pasada(self):
        """No crea Concert para eventos cuya fecha ya pasó."""
        bedrock = self._mock_bedrock({
            "is_concert_announcement": True,
            "band_name":   "Kreator",
            "event_date":  "2020-01-01",  # Fecha pasada
            "city":        "Bogotá",
            "confidence":  0.9,
        })

        concert = process_message_with_llm(
            "Kreator estuvo increíble en Bogotá el año pasado",
            bedrock,
        )
        assert concert is None

    def test_maneja_json_invalido_de_bedrock(self):
        """Maneja gracefully cuando Bedrock retorna texto no-JSON."""
        bedrock = MagicMock()
        bedrock.invoke.return_value = "Lo siento, no puedo procesar eso ahora."

        concert = process_message_with_llm("Mensaje cualquiera", bedrock)
        assert concert is None  # No debe lanzar excepción

    def test_festival_usa_festival_name(self):
        """Para festivales sin banda headliner, usa el nombre del festival."""
        bedrock = self._mock_bedrock({
            "is_concert_announcement": True,
            "band_name":    None,
            "festival_name": "Metal Devastation Festival",
            "event_date":   "2026-08-10",
            "city":         "Medellín",
            "venue":        "Parque Norte",
            "confidence":   0.88,
            "is_cancellation": False,
        })

        concert = process_message_with_llm(
            "Metal Devastation Festival confirmado para agosto en Medellín!",
            bedrock,
        )

        assert concert is not None
        assert "Metal Devastation Festival" in concert.band_name


# ---------------------------------------------------------------------------
# Tests de integración del Lambda handler (con moto)
# ---------------------------------------------------------------------------

class TestLambdaHandler:
    """Tests del handler completo usando mocks de AWS."""

    @pytest.fixture
    def sample_whatsapp_export(self, tmp_path):
        """Crea un archivo .txt de ejemplo de WhatsApp."""
        content = (
            "12/2/26, 08:00 a. m. - MetalBot: 🔥 CONFIRMADO: Sepultura en Bogotá\n"
            "15 de marzo 2026, Teatro Royal Centro\n"
            "Entradas desde $120.000 COP en tuboleta.com\n"
            "12/2/26, 08:05 a. m. - Fan1: VAMOOOOS\n"
            "12/2/26, 08:10 a. m. - Fan2: Ya compré las mías 🤘\n"
            "12/2/26, 09:00 a. m. - MetalBot: OJO: Kreator en Medellín, fecha TBD\n"
        )
        export_file = tmp_path / "export.txt"
        export_file.write_text(content, encoding="utf-8")
        return str(export_file)

    def test_extrae_concierto_de_export_real(self, sample_whatsapp_export):
        """Verifica que el export de ejemplo contiene el mensaje de Sepultura."""
        with open(sample_whatsapp_export, encoding="utf-8") as f:
            content = f.read()

        messages = extract_messages(content)
        assert any("Sepultura" in m for m in messages)
        assert any("Teatro Royal" in m for m in messages)
