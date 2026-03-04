"""
shared/bedrock_client.py
------------------------
Cliente centralizado para interactuar con Amazon Bedrock.
Todos los agentes usan este cliente en lugar de instanciar boto3 directamente.

Modelos disponibles en Bedrock:
  - anthropic.claude-sonnet-4-5  → El que usamos (balance precio/capacidad)
  - anthropic.claude-opus-4-5    → Más capaz, más caro (para tareas complejas)
  - anthropic.claude-haiku-4-5   → Más rápido y barato (para clasificaciones simples)
"""

import json
import logging
import os
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Modelos disponibles como constantes para fácil referencia
MODEL_SONNET = "anthropic.claude-sonnet-4-5-20251001"
MODEL_HAIKU = "anthropic.claude-haiku-4-5-20251001"


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
        model_id: str = MODEL_SONNET,
    ):
        self._region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._model_id = model_id
        self._client = boto3.client("bedrock-runtime", region_name=self._region)

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
            response = self._client.invoke_model(
                modelId=self._model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )

            response_body = json.loads(response["body"].read())
            content = response_body.get("content", [])

            if not content:
                logger.warning("Bedrock retornó respuesta vacía")
                return ""

            return content[0].get("text", "")

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
            response = self._client.invoke_model(
                modelId=self._model_id,
                body=json.dumps(body),
                contentType="application/json",
                accept="application/json",
            )

            response_body = json.loads(response["body"].read())
            content = response_body.get("content", [])

            return content[0].get("text", "") if content else ""

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
