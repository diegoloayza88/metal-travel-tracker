"""
dashboard/data/flights.py
-------------------------
Capa de datos para vuelos y precios históricos.
"""

import os
from datetime import datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Key

from src.shared.user_config import FLIGHT_ESTIMATE_USD, HOTEL_ESTIMATE_USD, BUY_WINDOW_FLIGHTS

TABLE_NAME = os.environ.get("DYNAMODB_TABLE_CONCERTS", "metal-travel-tracker-prod-concerts")
REGION = os.environ.get("AWS_REGION", "us-east-1")

COUNTRY_NAMES = {
    "CO": "🇨🇴 Colombia",
    "CL": "🇨🇱 Chile",
    "BR": "🇧🇷 Brasil",
    "US": "🇺🇸 Estados Unidos",
    "MX": "🇲🇽 México",
    "FI": "🇫🇮 Finlandia",
    "ES": "🇪🇸 España",
    "NO": "🇳🇴 Noruega",
    "DE": "🇩🇪 Alemania",
    "GR": "🇬🇷 Grecia",
    "RO": "🇷🇴 Rumanía",
}

# Mapa aeropuerto principal por país
MAIN_AIRPORT = {
    "CO": "BOG", "CL": "SCL", "BR": "GRU", "US": "JFK",
    "MX": "MEX", "FI": "HEL", "ES": "MAD", "NO": "OSL",
    "DE": "FRA", "GR": "ATH", "RO": "OTP",
}


def _get_table():
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    return dynamodb.Table(TABLE_NAME)


def get_historical_prices(origin: str = "LIM", destination: str = "BOG", lookback_days: int = 90) -> list[dict]:
    """Precios históricos de una ruta para graficar tendencia."""
    table = _get_table()
    from_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    try:
        response = table.query(
            KeyConditionExpression=(
                Key("pk").eq(f"PRICE#{origin}#{destination}")
                & Key("sk").gte(from_date)
            )
        )
        items = response.get("Items", [])
        return [
            {
                "fecha": item["sk"][:10],
                "precio_usd": float(item.get("price_usd", 0)),
                "aerolinea": item.get("airline", ""),
                "salida": item.get("departure_date", ""),
            }
            for item in sorted(items, key=lambda x: x["sk"])
        ]
    except Exception:
        return []


def get_all_routes_history(lookback_days: int = 90) -> list[dict]:
    """Todos los precios históricos disponibles (scan por pk prefix PRICE#)."""
    table = _get_table()
    from_date = (datetime.utcnow() - timedelta(days=lookback_days)).isoformat()
    try:
        from boto3.dynamodb.conditions import Attr
        response = table.scan(
            FilterExpression=(
                Attr("pk").begins_with("PRICE#LIM")
                & Attr("sk").gte(from_date)
            )
        )
        items = response.get("Items", [])
        result = []
        for item in items:
            parts = item.get("pk", "").split("#")
            if len(parts) >= 3:
                destination = parts[2]
                country = _airport_to_country(destination)
                result.append({
                    "ruta": f"LIM → {destination}",
                    "destino_iata": destination,
                    "country_code": country,
                    "país": COUNTRY_NAMES.get(country, country),
                    "fecha_registro": item["sk"][:10],
                    "precio_usd": float(item.get("price_usd", 0)),
                    "aerolinea": item.get("airline", ""),
                    "salida": item.get("departure_date", ""),
                })
        return sorted(result, key=lambda x: x["fecha_registro"])
    except Exception:
        return []


def get_budget_table() -> list[dict]:
    """Tabla de presupuestos estimados desde Lima para todos los países."""
    rows = []
    for cc, flag_name in COUNTRY_NAMES.items():
        flight = FLIGHT_ESTIMATE_USD.get(cc, (0, 0))
        hotel = HOTEL_ESTIMATE_USD.get(cc, (0, 0))
        buy_window = BUY_WINDOW_FLIGHTS.get(cc, "N/A")
        rows.append({
            "país": flag_name,
            "country_code": cc,
            "vuelo_min_usd": flight[0],
            "vuelo_max_usd": flight[1],
            "hotel_noche_min_usd": hotel[0],
            "hotel_noche_max_usd": hotel[1],
            "hotel_3n_min_usd": hotel[0] * 3,
            "hotel_3n_max_usd": hotel[1] * 3,
            "total_3n_min_usd": flight[0] + hotel[0] * 3,
            "total_3n_max_usd": flight[1] + hotel[1] * 3,
            "comprar_vuelo": buy_window,
            "aeropuerto": MAIN_AIRPORT.get(cc, ""),
        })
    return sorted(rows, key=lambda x: x["total_3n_min_usd"])


def _airport_to_country(iata: str) -> str:
    reverse = {v: k for k, v in MAIN_AIRPORT.items()}
    return reverse.get(iata, "")
