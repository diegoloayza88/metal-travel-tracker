"""
shared/bedrock_client.py
------------------------
Centralized client for interacting with Amazon Bedrock.
Every agent uses this client instead of instantiating boto3 directly.

Models (cross-region inference profiles, "us." prefix):
  - us.anthropic.claude-opus-5-5               -> Primary (BEDROCK_MODEL_ID)
  - us.anthropic.claude-sonnet-4-6              -> Fallback (BEDROCK_FALLBACK_MODEL_ID)
  - us.anthropic.claude-haiku-4-5-20251001-v1:0  -> Faster and cheaper (simple classifications)

If the primary model is not usable by the account (AccessDenied, e.g. its
on-demand quota is 0), the client switches to the fallback model and keeps
using it for the rest of the Lambda container's lifetime. Once AWS enables the
primary model, new containers pick it up automatically with no redeploy.
"""

import json
import logging
import os
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Modelos disponibles como constantes para fácil referencia
MODEL_OPUS = "us.anthropic.claude-opus-5-5"
MODEL_SONNET = "us.anthropic.claude-sonnet-4-6"
MODEL_HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Errores de Bedrock que indican "este modelo no está disponible para la cuenta"
# (no un fallo transitorio): justifican pasar al modelo de fallback.
_MODEL_UNAVAILABLE_ERRORS = {"AccessDeniedException", "ResourceNotFoundException"}

# Modelos que ya fallaron por acceso en este contenedor de Lambda; se evita
# reintentarlos en cada llamada (un reporte hace varias invocaciones).
_unavailable_models: set[str] = set()


class BedrockClient:
    """
    Cliente wrapper para Amazon Bedrock con manejo de errores y reintentos.

    Uso básico:
        bedrock = BedrockClient()
        response = bedrock.invoke("¿Qué bandas de metal tocan en Bogotá este año?")

    Uso con system prompt:
        response = bedrock.invoke(
            prompt="Analiza este mensaje...",
            system_prompt="Eres un experto en metal latinoamericano.",
        )
    """

    def __init__(
        self,
        region: Optional[str] = None,
        model_id: Optional[str] = None,
    ):
        self._region = region or os.environ.get("AWS_REGION", "us-east-1")
        # Un model_id explícito (ej. Haiku) manda; si no, el de Terraform.
        self._model_id = model_id or os.environ.get("BEDROCK_MODEL_ID", MODEL_OPUS)
        self._fallback_model_id = os.environ.get(
            "BEDROCK_FALLBACK_MODEL_ID", MODEL_SONNET
        )
        # boto3 corta la lectura a los 60s por defecto, y un reporte semanal
        # completo (~3500+ tokens de salida) tarda más que eso en generarse.
        # La Lambda tiene 900s de timeout, así que 300s por llamada es seguro.
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=self._region,
            config=Config(
                read_timeout=300,
                connect_timeout=10,
                retries={"max_attempts": 3, "mode": "adaptive"},
            ),
        )
        # Razón de corte de la última respuesta ("end_turn", "max_tokens", ...)
        self.last_stop_reason: Optional[str] = None

    def _candidate_models(self) -> list[str]:
        models = [self._model_id]
        if self._fallback_model_id and self._fallback_model_id != self._model_id:
            models.append(self._fallback_model_id)
        available = [m for m in models if m not in _unavailable_models]
        # Si todos fallaron antes, igual se reintenta el último en vez de no hacer nada.
        return available or models[-1:]

    def _invoke_body(self, body: dict) -> dict:
        """
        Invoca Bedrock con el modelo primario y, si la cuenta no tiene acceso
        a él, con el de fallback. Retorna el body JSON de la respuesta.
        """
        candidates = self._candidate_models()
        for i, model_id in enumerate(candidates):
            try:
                response = self._client.invoke_model(
                    modelId=model_id,
                    body=json.dumps(body),
                    contentType="application/json",
                    accept="application/json",
                )
                return json.loads(response["body"].read())
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                is_last = i == len(candidates) - 1
                if error_code in _MODEL_UNAVAILABLE_ERRORS and not is_last:
                    _unavailable_models.add(model_id)
                    logger.warning(
                        f"Modelo {model_id} no disponible para la cuenta "
                        f"({error_code}); usando fallback {candidates[i + 1]}"
                    )
                    continue
                raise
        raise BedrockInvocationError("Sin modelos candidatos")  # pragma: no cover

    def _extract_text(self, response_body: dict) -> str:
        self.last_stop_reason = response_body.get("stop_reason")
        if self.last_stop_reason == "max_tokens":
            logger.warning(
                "Respuesta de Bedrock cortada por max_tokens — el texto está incompleto"
            )
        content = response_body.get("content", [])
        if not content:
            logger.warning("Bedrock retornó respuesta vacía")
            return ""
        return content[0].get("text", "")

    # -------------------------------------------------------------------
    # Metodo principal de invocación
    # -------------------------------------------------------------------

    def invoke(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 1000,
        temperature: float = 0.1,  # Bajo para respuestas más determinísticas
    ) -> str:
        """
        Invoca el modelo de Bedrock con un mensaje simple.

        Args:
            prompt:        El mensaje del usuario.
            system_prompt: Instrucciones del sistema (contexto del agente).
            max_tokens:    Máximo de tokens en la respuesta.
            temperature:   0.0 = determinístico, 1.0 = más creativo.

        Returns:
            Texto de la respuesta del modelo.

        Raises:
            BedrockInvocationError si falla después de reintentos.
        """
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }

        if system_prompt:
            body["system"] = system_prompt

        try:
            return self._extract_text(self._invoke_body(body))

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            logger.error(f"Error de Bedrock ({error_code}): {e}")
            raise BedrockInvocationError(
                f"Error invocando Bedrock: {error_code}"
            ) from e

        except Exception as e:
            logger.error(f"Error inesperado invocando Bedrock: {e}")
            raise BedrockInvocationError(str(e)) from e

    def invoke_with_conversation(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ) -> str:
        """
        Invoca el modelo con una conversación multi-turno.
        Útil para el Orchestrator Agent que mantiene contexto entre pasos.

        Args:
            messages: Lista de mensajes en formato [{"role": "user/assistant", "content": "..."}]

        Returns:
            Texto de la respuesta del modelo.
        """
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }

        if system_prompt:
            body["system"] = system_prompt

        try:
            return self._extract_text(self._invoke_body(body))

        except Exception as e:
            logger.error(f"Error en invoke_with_conversation: {e}")
            raise BedrockInvocationError(str(e)) from e

    def classify_band_genre(self, band_name: str) -> list[str]:
        """
        Clasifica si una banda es de metal y determina sus géneros.
        Usa el modelo Haiku (más económico) para esta tarea simple.

        Args:
            band_name: Nombre de la banda a clasificar.

        Returns:
            Lista de géneros de metal, vacía si no es metal.
        """
        # Usar Haiku para clasificaciones simples (más barato)
        haiku_client = BedrockClient(model_id=MODEL_HAIKU)

        prompt = f"""¿Es "{band_name}" una banda de metal? Si es así, ¿de qué subgéneros?

Responde SOLO con JSON:
{{"is_metal": true/false, "genres": ["black_metal", "death_metal", "war_metal", "heavy_metal", "thrash_metal"]}}

Solo incluye géneros de esta lista exacta. Si no es metal, genres debe ser [].
Incluye solo los géneros que apliquen."""

        try:
            response = haiku_client.invoke(prompt, max_tokens=100, temperature=0.0)
            data = json.loads(response.strip())
            if data.get("is_metal"):
                return data.get("genres", [])
        except Exception:
            pass

        return []


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------


class BedrockInvocationError(Exception):
    """Error al invocar Amazon Bedrock."""

    pass
