"""Flask REST API for weather document synchronization."""

from __future__ import annotations

import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from lakebase import upsert_weather_documents
from weather_client import SUPPORTED_LOCATIONS, fetch_weather_documents

from ingest_weather_embeddings import run_ingest

load_dotenv()

app = Flask(__name__)


def _count_by_source(documents) -> dict[str, int]:
    counts = {"forecast": 0, "alert": 0}
    for document in documents:
        if document.source_type in counts:
            counts[document.source_type] += 1
    return counts

# To validate app deployment in databricks, should return status running
@app.get("/")
def home():
    return {
        "status":"running",
        "service":"Weather Vector Search",
        "nws_user_agent_configured": bool(os.getenv("NWS_USER_AGENT"))
    }


# Server Side Endpoint to sync weather documents
@app.post("/weather/sync")
@app.route("/api/sync", methods=["POST"])
def weather_sync():
    """Fetch NWS data for requested locations and upsert weather documents."""
    body = request.get_json(silent=True)
    if not body or "locations" not in body:
        return jsonify({"error": "Request body must include a 'locations' list."}), 400

    locations = body.get("locations")
    if not isinstance(locations, list) or not locations:
        return jsonify({"error": "'locations' must be a non-empty list."}), 400

    limit = body.get("limit")
    if limit is not None:
        if not isinstance(limit, int) or limit <= 0:
            return jsonify({"error": "'limit' must be a positive integer when provided."}), 400

    invalid_locations = [loc for loc in locations if loc not in SUPPORTED_LOCATIONS]
    if invalid_locations:
        return jsonify(
            {
                "error": "Unsupported location(s) requested.",
                "invalid_locations": invalid_locations,
                "supported_locations": sorted(SUPPORTED_LOCATIONS),
            }
        ), 400

    all_documents = []
    errors: list[dict[str, str]] = []

# Fetch weather documents for each location, return normalized documents
    for location in locations:
        try:
            documents = fetch_weather_documents(location)
            all_documents.extend(documents)
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            errors.append({"location": location, "message": str(exc)})

    if not all_documents and errors:
        return jsonify(
            {
                "error": "All requested locations failed before any documents could be synchronized.",
                "errors": errors,
            }
        ), 502

    if limit is not None:
        all_documents = all_documents[:limit]

# Upsert weather documents to the Lakebase database
    try:
        documents_synced = upsert_weather_documents(all_documents)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": f"Database upsert failed: {exc}"}), 500

    response = {
        "locations": locations,
        "documents_synced": documents_synced,
        "source_counts": _count_by_source(all_documents),
    }
    if errors:
        response["errors"] = errors

    return jsonify(response), 200


@app.get("/health")
def health():
    """Simple health check endpoint."""
    return jsonify({"status": "ok"})

# This API route is just an additional trigger because of the limitations encountered in Databricks Free Edition while executing the embedding ingestion notebook

@app.post("/api/embeddings/ingest")
def ingest_embeddings():
    try:
        summary = run_ingest()
        return jsonify(summary), 200
    except Exception as exc:
        return jsonify({
            "error": f"Embedding ingestion failed: {exc}"
        }), 500

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
