"""
shared/notifications.py
------------------------
Servicio centralizado para enviar notificaciones por los tres canales:
  1. SMS    → AWS SNS (Simple Notification Service)
  2. Email  → AWS SES (Simple Email Service) → tu Gmail
  3. Discord → Webhook directo

Todos los métodos retornan True/False para que el Reporter Agent
pueda loggear si algún canal falló sin detener la ejecución.
"""

import json
import logging
import os
import urllib.request
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Servicio de notificaciones multi-canal.
    
    Variables de entorno requeridas:
        SNS_PHONE_NUMBER      → Número de celular en formato E.164 (ej: +51999999999)
        SES_FROM_EMAIL        → Email verificado en SES (ej: tu_email@gmail.com)
        SES_TO_EMAIL          → Email destino (puede ser igual al from)
        DISCORD_WEBHOOK_URL   → URL del webhook de tu canal de Discord
        AWS_REGION            → Región de AWS (ej: us-east-1)
    """

    def __init__(self):
        region            = os.environ.get("AWS_REGION", "us-east-1")
        self._sns_client  = boto3.client("sns",  region_name=region)
        self._ses_client  = boto3.client("ses",  region_name=region)
        self._phone       = os.environ.get("SNS_PHONE_NUMBER", "")
        self._from_email  = os.environ.get("SES_FROM_EMAIL", "")
        self._to_email    = os.environ.get("SES_TO_EMAIL", "")
        self._discord_url = os.environ.get("DISCORD_WEBHOOK_URL", "")

    # -------------------------------------------------------------------
    # SMS via AWS SNS
    # -------------------------------------------------------------------

    def send_sms(self, message: str) -> bool:
        """
        Envía SMS al número de celular configurado.
        
        AWS SNS cobra por SMS enviado (~$0.00645 USD por SMS a Perú).
        
        Args:
            message: Texto del SMS. Máximo 160 chars para SMS simple.
                     SNS concatena automáticamente si es más largo.
        
        Returns:
            True si se envió exitosamente.
        """
        if not self._phone:
            logger.warning("SMS: SNS_PHONE_NUMBER no configurado, saltando")
            return False

        try:
            response = self._sns_client.publish(
                PhoneNumber=self._phone,
                Message=message,
                MessageAttributes={
                    "AWS.SNS.SMS.SMSType": {
                        "DataType":    "String",
                        "StringValue": "Transactional",  # Alta prioridad de entrega
                    },
                    "AWS.SNS.SMS.SenderID": {
                        "DataType":    "String",
                        "StringValue": "MetalTravel",  # Aparece como remitente
                    },
                },
            )
            logger.info(f"SMS enviado. MessageId: {response.get('MessageId')}")
            return True

        except ClientError as e:
            logger.error(f"Error enviando SMS via SNS: {e.response['Error']['Message']}")
            return False
        except Exception as e:
            logger.error(f"Error inesperado enviando SMS: {e}")
            return False

    # -------------------------------------------------------------------
    # Email via AWS SES
    # -------------------------------------------------------------------

    def send_email(
        self,
        subject: str,
        body_text: str,
        body_html: Optional[str] = None,
    ) -> bool:
        """
        Envía email via AWS SES.
        
        Prerequisito: El email de origen debe estar verificado en SES.
        Si tu cuenta está en sandbox, también el destino debe estar verificado.
        Para salir del sandbox: solicitar producción en la consola de SES.
        
        Args:
            subject:   Asunto del email.
            body_text: Cuerpo en texto plano (fallback para clientes sin HTML).
            body_html: Cuerpo en HTML (versión rica, opcional).
        
        Returns:
            True si se envió exitosamente.
        """
        if not self._from_email or not self._to_email:
            logger.warning("Email: SES_FROM_EMAIL o SES_TO_EMAIL no configurados, saltando")
            return False

        message_body = {
            "Text": {
                "Data":    body_text,
                "Charset": "UTF-8",
            }
        }

        if body_html:
            message_body["Html"] = {
                "Data":    body_html,
                "Charset": "UTF-8",
            }

        try:
            response = self._ses_client.send_email(
                Source=self._from_email,
                Destination={
                    "ToAddresses": [self._to_email],
                },
                Message={
                    "Subject": {
                        "Data":    subject,
                        "Charset": "UTF-8",
                    },
                    "Body": message_body,
                },
            )
            logger.info(f"Email enviado. MessageId: {response['MessageId']}")
            return True

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            logger.error(f"Error enviando email via SES ({error_code}): {e.response['Error']['Message']}")
            return False
        except Exception as e:
            logger.error(f"Error inesperado enviando email: {e}")
            return False

    # -------------------------------------------------------------------
    # Discord via Webhook
    # -------------------------------------------------------------------

    def send_discord(self, payload: dict) -> bool:
        """
        Envía un mensaje a Discord via webhook.
        
        El payload puede ser un mensaje simple o un embed complejo.
        Ver builder en reporter_agent/handler.py → build_discord_message().
        
        Args:
            payload: Dict con el payload del webhook de Discord.
                     Puede incluir 'content' (texto simple) o 'embeds' (ricos).
        
        Returns:
            True si Discord respondió con 2xx.
        """
        if not self._discord_url:
            logger.warning("Discord: DISCORD_WEBHOOK_URL no configurado, saltando")
            return False

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._discord_url,
                data=data,
                headers={
                    "Content-Type":   "application/json",
                    "User-Agent":     "MetalTravelTracker/1.0",
                },
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=15) as response:
                status = response.getcode()
                if status in (200, 204):
                    logger.info(f"Discord notificación enviada (HTTP {status})")
                    return True
                else:
                    logger.warning(f"Discord respondió con HTTP {status}")
                    return False

        except urllib.error.HTTPError as e:
            logger.error(f"Discord HTTP error {e.code}: {e.reason}")
            # Log del body para debugging
            try:
                body = e.read().decode("utf-8")
                logger.error(f"Discord error body: {body}")
            except Exception:
                pass
            return False
        except Exception as e:
            logger.error(f"Error enviando a Discord: {e}")
            return False

    # -------------------------------------------------------------------
    # Método conveniente para notificar errores del sistema
    # -------------------------------------------------------------------

    def send_error_alert(self, error_message: str, agent_name: str) -> None:
        """
        Notifica errores del sistema. Solo va a Discord (no spam al SMS/email).
        Útil para monitoreo de salud del sistema.
        """
        payload = {
            "embeds": [{
                "title":       f"⚠️ Error en {agent_name}",
                "description": f"```\n{error_message[:1900]}\n```",
                "color":       0xFF0000,
                "footer":      {"text": "Metal Travel Tracker — System Alert"},
            }]
        }
        self.send_discord(payload)
