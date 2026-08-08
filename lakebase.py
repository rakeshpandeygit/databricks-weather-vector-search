"""Lakebase/PostgreSQL connection helpers and weather document persistence."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import psycopg2
from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv
from psycopg2.extras import Json, execute_batch

from weather_client import WeatherDocument

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
_workspace = WorkspaceClient()

UPSERT_SQL = """
INSERT INTO weather_documents (
    id,
    location,
    source_type,
    headline,
    narrative_text,
    issued_at,
    effective_at,
    payload,
    synced_at
) VALUES (
    %(id)s,
    %(location)s,
    %(source_type)s,
    %(headline)s,
    %(narrative_text)s,
    %(issued_at)s,
    %(effective_at)s,
    %(payload)s,
    %(synced_at)s
)
ON CONFLICT (id) DO UPDATE SET
    location = EXCLUDED.location,
    source_type = EXCLUDED.source_type,
    headline = EXCLUDED.headline,
    narrative_text = EXCLUDED.narrative_text,
    issued_at = EXCLUDED.issued_at,
    effective_at = EXCLUDED.effective_at,
    payload = EXCLUDED.payload,
    synced_at = EXCLUDED.synced_at
"""


def get_lakebase_url() -> str:
    """Resolve the database URL from local env or Databricks secrets."""
    load_dotenv()

    url = os.getenv("LAKEBASE_URL")
    if url:
        return url

    scope = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
    key = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")

    try:
        secret = _workspace.secrets.get_secret(scope=scope, key=key)
        return base64.b64decode(secret.value).decode("utf-8")
    except Exception as exc:
        raise RuntimeError(
            "LAKEBASE_URL is not configured. Set LAKEBASE_URL in .env for local "
            f"development, or configure the Databricks secret {scope}/{key}."
        ) from exc


def get_connection():
    """Open a PostgreSQL connection to Lakebase."""
    return psycopg2.connect(get_lakebase_url())


def initialize_weather_documents() -> None:
    """Create weather_documents safely if it does not already exist."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(schema_sql)
        connection.commit()


def _document_to_row(document: WeatherDocument) -> dict:
    return {
        "id": document.id,
        "location": document.location,
        "source_type": document.source_type,
        "headline": document.headline,
        "narrative_text": document.narrative_text,
        "issued_at": document.issued_at,
        "effective_at": document.effective_at,
        "payload": Json(document.payload),
        "synced_at": document.synced_at,
    }


def upsert_weather_documents(documents: list[WeatherDocument]) -> int:
    """Insert or update weather documents without creating duplicates."""
    if not documents:
        return 0

    initialize_weather_documents()
    rows = [_document_to_row(document) for document in documents]

    with get_connection() as connection:
        with connection.cursor() as cursor:
            execute_batch(cursor, UPSERT_SQL, rows, page_size=100)
        connection.commit()

    return len(rows)
