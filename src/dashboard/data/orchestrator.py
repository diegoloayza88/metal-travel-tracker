"""
dashboard/data/orchestrator.py
-------------------------------
Permite disparar el orchestrator Lambda desde el dashboard.
"""

import json
import os

import boto3

ORCHESTRATOR_FUNCTION = os.environ.get(
    "ORCHESTRATOR_FUNCTION_NAME",
    "metal-travel-tracker-prod-orchestrator",
)
REGION = os.environ.get("AWS_REGION", "us-east-1")


def trigger_orchestrator(async_mode: bool = True) -> dict:
    """
    Invoca el orchestrator Lambda.
    async_mode=True → fire-and-forget (no espera resultado).
    """
    client = boto3.client("lambda", region_name=REGION)
    invocation_type = "Event" if async_mode else "RequestResponse"
    try:
        response = client.invoke(
            FunctionName=ORCHESTRATOR_FUNCTION,
            InvocationType=invocation_type,
            Payload=json.dumps({"source": "dashboard_manual"}),
        )
        status = response.get("StatusCode", 0)
        return {
            "success": status in (200, 202),
            "status_code": status,
            "message": "🤘 Orchestrator lanzado. Los resultados aparecerán en ~5 minutos."
            if async_mode
            else "Completado.",
        }
    except Exception as e:
        return {"success": False, "status_code": 0, "message": str(e)}


def get_last_runs(limit: int = 10) -> list[dict]:
    """Obtiene las últimas ejecuciones del orchestrator desde CloudWatch."""
    logs_client = boto3.client("logs", region_name=REGION)
    log_group = "/aws/lambda/metal-travel-tracker-prod-orchestrator"
    try:
        streams = logs_client.describe_log_streams(
            logGroupName=log_group,
            orderBy="LastEventTime",
            descending=True,
            limit=limit,
        )
        runs = []
        for stream in streams.get("logStreams", []):
            last_ts = stream.get("lastEventTimestamp", 0)
            if last_ts:
                from datetime import datetime
                dt = datetime.utcfromtimestamp(last_ts / 1000)
                runs.append({
                    "timestamp": dt.strftime("%Y-%m-%d %H:%M UTC"),
                    "stream": stream["logStreamName"],
                })
        return runs
    except Exception:
        return []
