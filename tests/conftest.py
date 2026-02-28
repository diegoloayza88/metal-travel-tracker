"""
tests/conftest.py
-----------------
Fixtures compartidos para todos los tests del proyecto.
pytest los carga automáticamente sin necesidad de importarlos.
"""

import os

import boto3
import pytest
from moto import mock_aws


# ---------------------------------------------------------------------------
# Configurar variables de entorno para tests (sin AWS real)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def aws_mock_env(monkeypatch):
    """
    Configura variables de entorno necesarias para tests.
    autouse=True significa que aplica automáticamente a todos los tests.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION",    "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID",     "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN",    "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN",     "testing")
    monkeypatch.setenv("ENVIRONMENT",           "test")
    monkeypatch.setenv("DYNAMODB_TABLE_CONCERTS", "metal-concerts-test")
    monkeypatch.setenv("DYNAMODB_TABLE_FLIGHTS",  "metal-flights-test")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL",   "https://discord.example.com/webhook")
    monkeypatch.setenv("SNS_PHONE_NUMBER",      "+51999999999")
    monkeypatch.setenv("SES_FROM_EMAIL",        "test@example.com")
    monkeypatch.setenv("SES_TO_EMAIL",          "test@example.com")


# ---------------------------------------------------------------------------
# Fixtures de DynamoDB mockeado con moto
# ---------------------------------------------------------------------------

@pytest.fixture
def dynamodb_concerts():
    """
    Crea una tabla DynamoDB de conciertos mockeada para tests.
    Se destruye automáticamente al finalizar el test.
    """
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="metal-concerts-test",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "source", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "source-index",
                "KeySchema": [{"AttributeName": "source", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


@pytest.fixture
def dynamodb_flights():
    """Crea una tabla DynamoDB de precios de vuelos mockeada."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="metal-flights-test",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        yield client


# ---------------------------------------------------------------------------
# Fixtures de datos de prueba
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_concert_dict():
    """Concert serializado como dict (simula lo que retorna DynamoDB)."""
    return {
        "pk":          "CONCERT#CO",
        "sk":          "2026-08-15#watain_2026-08-15_CO",
        "band_name":   "Watain",
        "event_date":  "2026-08-15",
        "city":        "Bogotá",
        "country":     "CO",
        "venue":       "Teatro Royal",
        "event_type":  "concert",
        "source":      "songkick",
        "source_tier": "1",
        "confidence":  "0.95",
    }
