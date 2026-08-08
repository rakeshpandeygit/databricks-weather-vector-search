"""Harvest and normalize weather data from the National Weather Service API."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv

NWS_BASE_URL = "https://api.weather.gov"

SUPPORTED_LOCATIONS: dict[str, dict[str, float]] = {
    "Chicago, IL": {"lat": 41.8781, "lon": -87.6298},
    "Austin, TX": {"lat": 30.2672, "lon": -97.7431},
}

DEFAULT_TIMEOUT = (10, 30)


@dataclass
class WeatherDocument:
    id: str
    location: str
    source_type: str
    headline: str
    narrative_text: str
    issued_at: datetime | None
    effective_at: datetime | None
    payload: dict
    synced_at: datetime


def _nws_headers() -> dict[str, str]:
    load_dotenv()
    user_agent = os.getenv("NWS_USER_AGENT")
    if not user_agent:
        raise RuntimeError(
            "NWS_USER_AGENT is not configured. "
            "Set NWS_USER_AGENT in your environment or .env file per NWS API policy."
        )
    return {
        "User-Agent": user_agent,
        "Accept": "application/geo+json",
    }


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _join_narrative_parts(*parts: str | None) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "\n\n".join(cleaned)


def _request_json(url: str) -> dict[str, Any]:
    response = requests.get(url, headers=_nws_headers(), timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.json()


def get_point_metadata(lat: float, lon: float) -> dict[str, Any]:
    """Resolve NWS grid metadata and forecast URL for a latitude/longitude pair."""
    url = f"{NWS_BASE_URL}/points/{lat},{lon}"
    return _request_json(url)


def fetch_forecast(forecast_url: str) -> dict[str, Any]:
    """Fetch the multi-day forecast JSON from the URL returned by /points."""
    return _request_json(forecast_url)


def fetch_alerts(lat: float, lon: float) -> dict[str, Any]:
    """Fetch point-specific active alerts for a latitude/longitude pair."""
    url = f"{NWS_BASE_URL}/alerts/active?point={lat},{lon}"
    return _request_json(url)


def normalize_alert(feature: dict[str, Any], location: str, synced_at: datetime) -> WeatherDocument | None:
    """Convert one NWS alert GeoJSON feature into a WeatherDocument."""
    properties = feature.get("properties", {})
    narrative_text = _join_narrative_parts(
        properties.get("description"),
        properties.get("instruction"),
    )
    if not narrative_text:
        return None

    alert_id = feature.get("id") or properties.get("id") or properties.get("@id")
    if not alert_id:
        return None

    headline = properties.get("event") or properties.get("headline") or "Weather Alert"

    return WeatherDocument(
        id=str(alert_id),
        location=location,
        source_type="alert",
        headline=headline,
        narrative_text=narrative_text,
        issued_at=_parse_timestamp(properties.get("sent")),
        effective_at=_parse_timestamp(properties.get("effective")),
        payload=feature,
        synced_at=synced_at,
    )


def normalize_forecast_period(
    period: dict[str, Any],
    location: str,
    issued_at: datetime | None,
    synced_at: datetime,
) -> WeatherDocument | None:
    """Convert one forecast period into a WeatherDocument."""
    detailed_forecast = (period.get("detailedForecast") or "").strip()
    if not detailed_forecast:
        return None

    start_time = period.get("startTime")
    if not start_time:
        return None

    name = period.get("name", "Forecast")
    short_forecast = period.get("shortForecast", "")
    headline = f"{name} - {short_forecast}".strip(" -")

    return WeatherDocument(
        id=f"{location}|{start_time}",
        location=location,
        source_type="forecast",
        headline=headline,
        narrative_text=detailed_forecast,
        issued_at=issued_at,
        effective_at=_parse_timestamp(start_time),
        payload=period,
        synced_at=synced_at,
    )


def fetch_weather_documents(location: str) -> list[WeatherDocument]:
    """Fetch and normalize forecast + point-specific alert documents for one location."""
    if location not in SUPPORTED_LOCATIONS:
        raise ValueError(
            f"Unsupported location: {location}. "
            f"Supported locations: {', '.join(sorted(SUPPORTED_LOCATIONS))}"
        )

    coords = SUPPORTED_LOCATIONS[location]
    lat = coords["lat"]
    lon = coords["lon"]
    synced_at = datetime.now(timezone.utc)
    documents: list[WeatherDocument] = []

    point_data = get_point_metadata(lat, lon)
    forecast_url = point_data.get("properties", {}).get("forecast")
    if not forecast_url:
        raise RuntimeError(f"No forecast URL returned for {location}")

# fetch Forecasted document
    forecast_data = fetch_forecast(forecast_url)
    forecast_properties = forecast_data.get("properties", {})
    issued_at = _parse_timestamp(
        forecast_properties.get("generatedAt") or forecast_properties.get("updated")
    )

    for period in forecast_properties.get("periods", []):
        document = normalize_forecast_period(period, location, issued_at, synced_at)
        if document:
            documents.append(document)

# fetch Alerts document
    alerts_data = fetch_alerts(lat, lon)
    for feature in alerts_data.get("features", []):
        document = normalize_alert(feature, location, synced_at)
        if document:
            documents.append(document)

    return documents


def _format_document_sample(document: WeatherDocument) -> str:
    narrative_snippet = document.narrative_text[:160]
    if len(document.narrative_text) > 160:
        narrative_snippet += "..."

    return (
        f"ID: {document.id}\n"
        f"location: {document.location}\n"
        f"source_type: {document.source_type}\n"
        f"headline: {document.headline}\n"
        f"narrative: {narrative_snippet}\n"
        f"issued_at: {document.issued_at}\n"
        f"effective_at: {document.effective_at}\n"
    )


def main() -> None:
    """Local learning test: harvest Chicago weather without Lakebase."""
    load_dotenv()

    location = "Chicago, IL"
    print(f"Fetching NWS weather documents for {location}...\n")

    try:
        documents = fetch_weather_documents(location)
    except requests.HTTPError as exc:
        print(f"NWS API request failed: {exc}", file=sys.stderr)
        if exc.response is not None:
            print(f"Response body: {exc.response.text[:500]}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    forecast_count = sum(1 for doc in documents if doc.source_type == "forecast")
    alert_count = sum(1 for doc in documents if doc.source_type == "alert")
    print(
        f"Normalized {len(documents)} documents "
        f"({forecast_count} forecast, {alert_count} alert)\n"
    )

    samples = documents[:3]
    for index, document in enumerate(samples, start=1):
        print(f"--- Sample {index} ---")
        print(_format_document_sample(document))


if __name__ == "__main__":
    main()
