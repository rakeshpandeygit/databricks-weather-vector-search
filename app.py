"""Flask REST API for weather document synchronization."""

from __future__ import annotations

import os

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from lakebase import search_weather_embeddings, upsert_weather_documents
from weather_client import SUPPORTED_LOCATIONS, fetch_weather_documents

from ingest_weather_embeddings import MODEL_NAME, embed_query, run_ingest

load_dotenv()

app = Flask(__name__)

ALLOWED_SOURCE_TYPES = {"alert", "forecast"}


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
@app.post("/api/sync")
#@app.route("/api/sync", methods=["POST"])
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


def _serialize_effective_at(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _serialize_search_result(result) -> dict:
    distance = result.distance
    return {
        "document_id": result.document_id,
        "chunk_index": result.chunk_index,
        "location": result.location,
        "source_type": result.source_type,
        "headline": result.headline,
        "chunk_text": result.chunk_text,
        "distance": distance,
        "similarity": 1.0 - distance,
        "effective_at": _serialize_effective_at(result.effective_at),
    }


@app.post("/weather/search")
@app.post("/api/search")
def search_weather():
    """Embed a query and return the closest weather narrative chunks."""
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON."}), 400

    query = body.get("query")
    if not isinstance(query, str) or not query.strip():
        return jsonify({"error": "'query' must be a non-empty string."}), 400

    top_k = body.get("top_k", 5)
    if not isinstance(top_k, int) or top_k < 1 or top_k > 20:
        return jsonify({"error": "'top_k' must be an integer between 1 and 20."}), 400

    source_type = body.get("source_type")
    if source_type is not None:
        if not isinstance(source_type, str) or source_type not in ALLOWED_SOURCE_TYPES:
            return jsonify(
                {
                    "error": "'source_type' must be 'alert' or 'forecast' when provided.",
                    "allowed_values": sorted(ALLOWED_SOURCE_TYPES),
                }
            ), 400

    query_text = query.strip()

    try:
        query_embedding = embed_query(query_text)
        results = search_weather_embeddings(
            query_embedding,
            MODEL_NAME,
            top_k,
            source_type=source_type,
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except Exception as exc:
        return jsonify({"error": f"Search failed: {exc}"}), 500

    response = {
        "query": query_text,
        "top_k": top_k,
        "results": [_serialize_search_result(result) for result in results],
    }
    if source_type is not None:
        response["source_type"] = source_type

    return jsonify(response), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.getenv("FLASK_DEBUG") == "1")
