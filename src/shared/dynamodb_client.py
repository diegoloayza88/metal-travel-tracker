"""
shared/dynamodb_client.py
-------------------------
Cliente centralizado para operaciones en DynamoDB.
Abstrae las operaciones CRUD comunes para que los agentes no interactúen
directamente con boto3 y el código quede más limpio y testeable.

Tablas utilizadas:
  - metal-concerts-{env}    → Conciertos encontrados por los plugins
  - metal-flights-{env}     → Vuelos encontrados y su histórico de precios
  - metal-hotels-{env}      → Opciones de alojamiento
  - metal-deals-{env}       → Deals finales que fueron notificados
"""

import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr, Key

from src.models.concert import Concert, Flight

logger = logging.getLogger(__name__)


class DynamoDBClient:
    """
    Cliente de DynamoDB para el proyecto Metal Travel Tracker.

    Uso:
        db = DynamoDBClient(table_name="metal-concerts-prod")
        db.save_concert(concert)
        concerts = db.get_upcoming_concerts(country="CO", days_ahead=90)
    """

    def __init__(self, table_name: str):
        # Terraform already passes the full table name (e.g. metal-travel-tracker-prod-concerts)
        # via DYNAMODB_TABLE_CONCERTS / DYNAMODB_TABLE_FLIGHTS env vars, so use it as-is.
        self._table_name = table_name

        dynamodb = boto3.resource(
            "dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")
        )
        self._table = dynamodb.Table(self._table_name)

    # -------------------------------------------------------------------
    # Operaciones de Conciertos
    # -------------------------------------------------------------------

    def save_concert(self, concert: Concert) -> bool:
        """
        Guarda un concierto en DynamoDB.
        Si ya existe (mismo unique_key), actualiza en lugar de duplicar.

        Returns:
            True si se guardó exitosamente.
        """
        try:
            item = concert.to_dynamodb_item()
            # Agregar TTL: los conciertos expiran 30 días después del evento
            event_datetime = datetime.combine(concert.event_date, datetime.min.time())
            ttl = int((event_datetime + timedelta(days=30)).timestamp())
            item["ttl"] = {"N": str(ttl)}

            self._table.put_item(Item=self._deserialize_item(item))
            return True
        except Exception as e:
            logger.error(f"Error guardando concierto {concert.unique_key}: {e}")
            return False

    def exists(self, unique_key: str) -> bool:
        """
        Verifica si un concierto ya existe para evitar duplicados.

        Args:
            unique_key: El campo unique_key del modelo Concert.
        """
        try:
            response = self._table.query(
                FilterExpression=Attr("sk").contains(unique_key),
                Limit=1,
            )
            return len(response.get("Items", [])) > 0
        except Exception as e:
            logger.warning(f"Error verificando existencia de {unique_key}: {e}")
            return False

    def get_upcoming_concerts(
        self,
        country: Optional[str] = None,
        days_ahead: int = 180,
        min_confidence: float = 0.7,
    ) -> list[dict]:
        """
        Obtiene conciertos futuros desde DynamoDB.

        Args:
            country:        Código de país (ej: "CO", "CL"). None = todos.
            days_ahead:     Cuántos días hacia el futuro buscar.
            min_confidence: Confianza mínima (relevante para fuentes Tier 3).

        Returns:
            Lista de items de DynamoDB (dicts).
        """
        today = date.today()
        max_date = today + timedelta(days=days_ahead)

        try:
            if country:
                # Buscar por país específico (pk)
                response = self._table.query(
                    KeyConditionExpression=(
                        Key("pk").eq(f"CONCERT#{country}")
                        & Key("sk").between(
                            f"{today.strftime('%Y-%m-%d')}",
                            f"{max_date.strftime('%Y-%m-%d')}~",
                        )
                    ),
                    FilterExpression=Attr("confidence").gte(
                        Decimal(str(min_confidence))
                    ),
                )
            else:
                # Scan de todos los países (menos eficiente, usar con moderación)
                response = self._table.scan(
                    FilterExpression=(
                        Attr("event_date").between(
                            today.strftime("%Y-%m-%d"),
                            max_date.strftime("%Y-%m-%d"),
                        )
                        & Attr("confidence").gte(Decimal(str(min_confidence)))
                    )
                )

            return response.get("Items", [])

        except Exception as e:
            logger.error(f"Error consultando conciertos: {e}")
            return []

    def get_concerts_needing_flight_search(self, days_ahead: int = 180) -> list[dict]:
        """
        Retorna conciertos que aún no tienen vuelo asociado buscado.
        El Flight Agent llama este metodo para saber qué buscar hoy.
        """
        try:
            response = self._table.scan(
                FilterExpression=(
                    Attr("flight_searched").not_exists()
                    & Attr("event_date").gte(
                        (date.today() + timedelta(days=14)).strftime("%Y-%m-%d")
                    )
                )
            )
            return response.get("Items", [])
        except Exception as e:
            logger.error(f"Error consultando conciertos sin vuelo: {e}")
            return []

    def mark_flight_searched(self, concert_unique_key: str):
        """Marca que ya se buscó vuelo para este concierto hoy."""
        today = date.today().isoformat()
        try:
            # Buscar el item primero
            response = self._table.scan(
                FilterExpression=Attr("sk").contains(concert_unique_key),
                Limit=1,
            )
            items = response.get("Items", [])
            if items:
                item = items[0]
                self._table.update_item(
                    Key={"pk": item["pk"], "sk": item["sk"]},
                    UpdateExpression="SET flight_searched = :date",
                    ExpressionAttributeValues={":date": today},
                )
        except Exception as e:
            logger.warning(
                f"Error marcando flight_searched para {concert_unique_key}: {e}"
            )

    # -------------------------------------------------------------------
    # Operaciones de Precios Históricos (para análisis de deals)
    # -------------------------------------------------------------------

    def save_flight_price(self, flight: Flight) -> bool:
        """
        Guarda el precio de un vuelo para construir el histórico.
        El histórico se usa para calcular si el precio actual es un deal.
        """
        try:
            self._table.put_item(
                Item={
                    "pk": f"PRICE#{flight.origin}#{flight.destination}",
                    "sk": flight.found_at.isoformat(),
                    "price_usd": Decimal(str(flight.price_usd)),
                    "airline": flight.airline,
                    "departure_date": flight.departure_date.isoformat(),
                    "source": flight.source,
                    "ttl": int((datetime.utcnow() + timedelta(days=90)).timestamp()),
                }
            )
            return True
        except Exception as e:
            logger.error(f"Error guardando precio de vuelo: {e}")
            return False

    def get_historical_prices(
        self,
        origin: str,
        destination: str,
        lookback_days: int = 60,
    ) -> list[float]:
        """
        Obtiene precios históricos de una ruta para calcular percentiles.

        Returns:
            Lista de precios en USD de los últimos N días.
        """
        from_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()

        try:
            response = self._table.query(
                KeyConditionExpression=(
                    Key("pk").eq(f"PRICE#{origin}#{destination}")
                    & Key("sk").gte(from_date)
                )
            )
            return [float(item["price_usd"]) for item in response.get("Items", [])]
        except Exception as e:
            logger.error(
                f"Error consultando precios históricos {origin}→{destination}: {e}"
            )
            return []

    # -------------------------------------------------------------------
    # Utilidades internas
    # -------------------------------------------------------------------

    @staticmethod
    def _deserialize_item(dynamo_item: dict) -> dict:
        """
        Convierte el formato de bajo nivel de DynamoDB ({"S": "valor"})
        al formato de alto nivel que espera boto3 resource ({"key": "valor"}).
        """
        result = {}
        for key, value_dict in dynamo_item.items():
            if "S" in value_dict:
                result[key] = value_dict["S"]
            elif "N" in value_dict:
                # Usar Decimal para números (requerido por DynamoDB)
                result[key] = Decimal(value_dict["N"])
            elif "SS" in value_dict:
                result[key] = set(value_dict["SS"])
            elif "BOOL" in value_dict:
                result[key] = value_dict["BOOL"]
        return result
