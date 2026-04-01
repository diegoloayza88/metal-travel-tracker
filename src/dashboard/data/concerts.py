"""
dashboard/data/concerts.py
--------------------------
Capa de datos para conciertos. Puede reutilizarse como Lambda handler
de API Gateway en la futura migración a React.
"""

import os
from datetime import date, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key

TABLE_NAME = os.environ.get("DYNAMODB_TABLE_CONCERTS", "metal-travel-tracker-prod-concerts")
REGION = os.environ.get("AWS_REGION", "us-east-1")

ALL_COUNTRIES = ["CO", "CL", "BR", "US", "MX", "FI", "ES", "NO", "DE", "GR", "RO"]

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


def _get_table():
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    return dynamodb.Table(TABLE_NAME)


def get_all_concerts(
    countries: list[str] | None = None,
    days_ahead: int = 365,
    min_confidence: float = 0.5,
    watchlist_only: bool = False,
) -> list[dict]:
    """
    Retorna todos los conciertos próximos, opcionalmente filtrados por país.
    Normaliza los campos para que sean seguros en Pandas/Streamlit.
    """
    table = _get_table()
    today = date.today()
    max_date = today + timedelta(days=days_ahead)
    target_countries = countries or ALL_COUNTRIES

    all_items: list[dict] = []
    for country in target_countries:
        try:
            response = table.query(
                KeyConditionExpression=(
                    Key("pk").eq(f"CONCERT#{country}")
                    & Key("sk").between(
                        today.strftime("%Y-%m-%d"),
                        max_date.strftime("%Y-%m-%d") + "~",
                    )
                ),
                FilterExpression=Attr("confidence").gte(Decimal(str(min_confidence))),
            )
            all_items.extend(response.get("Items", []))
        except Exception:
            continue

    # Normalizar
    concerts = []
    for item in all_items:
        country_code = item.get("pk", "").replace("CONCERT#", "")
        score = float(item.get("watchlist_score", 0) or 0)
        if watchlist_only and score == 0:
            continue
        source = item.get("source", "")
        # festival_name se guarda directamente; fallback: si source=festivals usamos band_name como festival
        festival_name = item.get("festival_name", "")
        if not festival_name and source == "festivals":
            festival_name = item.get("band_name", "")

        concerts.append({
            "país": COUNTRY_NAMES.get(country_code, country_code),
            "country_code": country_code,
            "banda": item.get("band_name", ""),
            "fecha": item.get("event_date", ""),
            "ciudad": item.get("city", ""),
            "venue": item.get("venue", ""),
            "festival": festival_name,
            "fuente": source,
            "watchlist_score": score,
            "watchlist_match": bool(item.get("watchlist_match", False)),
            "ticket_url": item.get("ticket_url", ""),
            "confidence": float(item.get("confidence", 0) or 0),
        })

    concerts.sort(key=lambda x: x["fecha"])
    return concerts


def get_festivals(days_ahead: int = 365) -> list[dict]:
    """
    Retorna festivales agrupados. Dos estrategias:
    1. Si festival_name está en DynamoDB → agrupar por ese campo.
    2. Si source="festivals" y no hay festival_name → agrupar por (fecha, ciudad, país)
       para reconstruir el festival (el FestivalsPlugin guarda una banda por item).
    """
    all_concerts = get_all_concerts(days_ahead=days_ahead, min_confidence=0.0)
    festival_map: dict[str, dict] = {}

    for c in all_concerts:
        festival_name = c.get("festival", "")
        fuente = c.get("fuente", "")

        # Caso 1: festival_name guardado explícitamente
        if festival_name and festival_name != c["banda"]:
            key = festival_name

        # Caso 2: source=festivals sin festival_name → agrupar por fecha+ciudad+país
        elif fuente == "festivals":
            key = f"{c['fecha']}|{c['ciudad']}|{c['country_code']}"
            festival_name = f"Festival en {c['ciudad']} ({c['fecha']})"

        else:
            continue  # no es un festival

        if key not in festival_map:
            festival_map[key] = {
                "festival": festival_name,
                "país": c["país"],
                "country_code": c["country_code"],
                "ciudad": c["ciudad"],
                "fecha": c["fecha"],
                "ticket_url": c["ticket_url"],
                "bandas": [],
                "watchlist_matches": [],
            }

        band = c["banda"]
        if band and not band.startswith("["):
            festival_map[key]["bandas"].append(band)
        if c["watchlist_score"] > 0:
            festival_map[key]["watchlist_matches"].append(band)

    # Para los grupos por fecha+ciudad, intentar inferir nombre del festival
    # (el FestivalsPlugin conoce el nombre pero no lo guardaba antes del fix)
    _KNOWN_FESTIVALS = {
        ("2026-04-15", "Oslo"): "Inferno Metal Fest 2026",
        ("2026-05-15", "Hyvinkää"): "Steelfest 2026",
        ("2026-04-24", "Lauda-Königshofen"): "Keep It True Rising 2026",
        ("2026-05-22", "Baltimore"): "Maryland Deathfest 2026",
        ("2026-03-27", "Athens"): "Up the Hammers 2026",
        ("2026-04-17", "Houston"): "Hell's Heroes 2026",
        ("2026-09-06", "Pryor"): "Rocklahoma 2026",
    }
    for item in festival_map.values():
        lookup = (item["fecha"], item["ciudad"])
        if lookup in _KNOWN_FESTIVALS:
            item["festival"] = _KNOWN_FESTIVALS[lookup]

    return sorted(festival_map.values(), key=lambda x: x["fecha"])


def get_concert_stats() -> dict:
    """Estadísticas rápidas para el header del dashboard."""
    concerts = get_all_concerts(min_confidence=0.0)
    by_country = {}
    for c in concerts:
        cc = c["country_code"]
        by_country[cc] = by_country.get(cc, 0) + 1

    watchlist_hits = [c for c in concerts if c["watchlist_score"] > 0]
    festivals = [c for c in concerts if c["festival"]]

    return {
        "total": len(concerts),
        "by_country": by_country,
        "watchlist_hits": len(watchlist_hits),
        "festival_concerts": len(festivals),
        "countries_with_data": len(by_country),
    }
