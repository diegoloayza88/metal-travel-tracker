"""
shared/secrets.py
-----------------
Utility to load API credentials from AWS Secrets Manager into environment
variables at Lambda startup. Call load_secrets() once at the top of each
lambda_handler so every plugin and client can read keys via os.environ.
"""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)


def load_secrets() -> None:
    """
    Reads the secret stored in SECRETS_ARN and injects each key/value pair
    into os.environ (only if not already set, so local overrides are respected).
    """
    secrets_arn = os.environ.get("SECRETS_ARN")
    if not secrets_arn:
        logger.warning("SECRETS_ARN no configurado, omitiendo carga de secretos")
        return

    try:
        client = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
        response = client.get_secret_value(SecretId=secrets_arn)
        secrets = json.loads(response["SecretString"])
        for key, value in secrets.items():
            if value:  # don't overwrite with empty strings
                os.environ.setdefault(key, value)
        logger.info(f"Secretos cargados desde Secrets Manager ({len(secrets)} keys)")
    except Exception as e:
        logger.error(f"Error cargando secretos desde Secrets Manager: {e}")
