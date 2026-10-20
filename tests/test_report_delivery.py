"""
tests/test_report_delivery.py
-----------------------------
Tests de la entrega del reporte: partición para Discord, HTML del email,
fallback de modelo de Bedrock y manejo de rate limit de Discord.
"""

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

from src.agents.reporter_agent.handler import (
    DISCORD_EMBED_DESCRIPTION_LIMIT,
    build_discord_messages,
    markdown_to_html,
    split_text,
)
from src.shared import bedrock_client
from src.shared.bedrock_client import BedrockClient
from src.shared.notifications import NotificationService

# ---------------------------------------------------------------------------
# Partición del texto
# ---------------------------------------------------------------------------


class TestSplitText:
    def test_texto_corto_queda_en_un_trozo(self):
        assert split_text("hola\nmundo", 100) == ["hola\nmundo"]

    def test_respeta_el_limite_y_no_pierde_contenido(self):
        text = "\n".join(f"Línea número {i} del reporte semanal" for i in range(500))
        chunks = split_text(text, 1000)
        assert len(chunks) > 1
        assert all(len(c) <= 1000 for c in chunks)
        assert "\n".join(chunks) == text

    def test_no_corta_lineas_a_la_mitad(self):
        text = "\n".join("x" * 90 for _ in range(30))
        for chunk in split_text(text, 200):
            assert all(len(line) == 90 for line in chunk.split("\n"))

    def test_linea_mas_larga_que_el_limite_se_parte(self):
        chunks = split_text("a" * 250, 100)
        assert [len(c) for c in chunks] == [100, 100, 50]


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


class TestBuildDiscordMessages:
    def test_reporte_largo_se_envia_completo_en_varios_mensajes(self):
        report = "\n\n".join(f"## Sección {i}\n" + "texto " * 150 for i in range(12))
        messages = build_discord_messages(report, [], "2026-10-19")

        assert len(messages) > 1
        descriptions = [m["embeds"][0]["description"] for m in messages]
        assert all(len(d) <= DISCORD_EMBED_DESCRIPTION_LIMIT for d in descriptions)
        # Nada se pierde: el contenido completo llega a Discord
        assert "\n".join(descriptions).replace("\n", "") == report.replace("\n", "")
        assert messages[0]["embeds"][0]["title"].endswith(f"(1/{len(messages)})")

    def test_reporte_corto_es_un_solo_mensaje_sin_numeracion(self):
        messages = build_discord_messages("Reporte corto", [], "2026-10-19")
        assert len(messages) == 1
        assert "(1/1)" not in messages[0]["embeds"][0]["title"]

    def test_deals_van_en_mensaje_aparte_dentro_del_limite_total(self):
        deals = [
            {
                "destination": "BOG",
                "deal_quality": "EXCELLENT",
                "price_usd": 210,
                "discount_pct": 32.0,
                "departure_date": "2026-11-01",
                "return_date": "2026-11-05",
                "airline": "AV",
                "booking_url": "https://example.com",
            }
        ]
        messages = build_discord_messages("x " * 1990, deals, "2026-10-19")
        assert messages[-1]["embeds"][0]["fields"][0]["name"].startswith("✈️ LIM → BOG")
        for message in messages:
            assert len(json.dumps(message["embeds"], ensure_ascii=False)) < 6000
        assert messages[0]["embeds"][0]["color"] == 0xFF0000


# ---------------------------------------------------------------------------
# Email HTML
# ---------------------------------------------------------------------------


class TestMarkdownToHtml:
    def test_convierte_titulos_listas_y_links(self):
        html = markdown_to_html(
            "### Watchlist\n- **Mayhem** en Oslo\n- Ver [tickets](https://t.co/x)"
        )
        assert "<h3" in html and "Watchlist</h3>" in html
        assert "<ul" in html and html.count("<li") == 2
        assert "<strong>Mayhem</strong>" in html
        assert '<a href="https://t.co/x"' in html

    def test_escapa_html_del_texto(self):
        html = markdown_to_html("precio < 300 & fecha <script>")
        assert "&lt; 300 &amp; fecha &lt;script&gt;" in html
        assert "<script>" not in html

    def test_urls_sueltas_se_vuelven_links(self):
        html = markdown_to_html("Compra en https://booking.com/x hoy")
        assert '<a href="https://booking.com/x"' in html


# ---------------------------------------------------------------------------
# Bedrock: fallback de modelo
# ---------------------------------------------------------------------------


def _bedrock_response(text: str, stop_reason: str = "end_turn") -> dict:
    body = json.dumps(
        {"content": [{"text": text}], "stop_reason": stop_reason}
    ).encode()
    return {"body": io.BytesIO(body)}


def _access_denied() -> ClientError:
    return ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no access"}},
        "InvokeModel",
    )


class TestBedrockFallback:
    def setup_method(self):
        bedrock_client._unavailable_models.clear()

    def test_usa_fallback_si_la_cuenta_no_tiene_acceso_al_primario(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "us.primary")
        monkeypatch.setenv("BEDROCK_FALLBACK_MODEL_ID", "us.fallback")
        client = BedrockClient()
        client._client = MagicMock()
        client._client.invoke_model.side_effect = [
            _access_denied(),
            _bedrock_response("hola"),
            _bedrock_response("otra vez"),
        ]

        assert client.invoke("prompt") == "hola"
        # La segunda llamada va directo al fallback, sin reintentar el primario
        assert client.invoke("prompt") == "otra vez"
        used = [c.kwargs["modelId"] for c in client._client.invoke_model.call_args_list]
        assert used == ["us.primary", "us.fallback", "us.fallback"]

    def test_registra_stop_reason(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "us.primary")
        client = BedrockClient()
        client._client = MagicMock()
        client._client.invoke_model.return_value = _bedrock_response(
            "texto cortado", "max_tokens"
        )
        client.invoke("prompt")
        assert client.last_stop_reason == "max_tokens"


# ---------------------------------------------------------------------------
# Discord: rate limit
# ---------------------------------------------------------------------------


class TestDiscordRateLimit:
    def test_reintenta_despues_de_429(self):
        notifier = NotificationService()
        rate_limited = urllib.error.HTTPError(
            "url", 429, "Too Many Requests", {}, io.BytesIO(b'{"retry_after": 0.01}')
        )
        ok = MagicMock()
        ok.__enter__.return_value.getcode.return_value = 204

        with (
            patch("urllib.request.urlopen", side_effect=[rate_limited, ok]),
            patch("src.shared.notifications.time.sleep") as sleep,
        ):
            assert notifier.send_discord({"content": "hola"}) is True
        sleep.assert_called_once_with(0.01)
