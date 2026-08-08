"""Lakebase/PostgreSQL connection helpers and weather document persistence."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2
from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv
from pgvector.psycopg2 import register_vector
from psycopg2.extras import Json, RealDictCursor, execute_batch

from weather_client import WeatherDocument

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
EMBEDDINGS_SCHEMA_PATH = Path(__file__).resolve().parent / "schema_embeddings.sql"
_workspace: WorkspaceClient | None = None


def _get_workspace() -> WorkspaceClient:
    """Create the Databricks client only when a secret lookup is required."""
    global _workspace
    if _workspace is None:
        _workspace = WorkspaceClient()
    return _workspace

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
WHERE weather_documents.narrative_text IS DISTINCT FROM EXCLUDED.narrative_text
"""

DOCUMENTS_NEEDING_EMBEDDINGS_SQL = """
SELECT
    wd.id,
    wd.narrative_text,
    wd.synced_at
FROM weather_documents wd
WHERE TRIM(wd.narrative_text) <> ''
  AND (
    NOT EXISTS (
        SELECT 1
        FROM weather_embeddings we
        WHERE we.document_id = wd.id
          AND we.model_name = %(model_name)s
    )
    OR wd.synced_at > (
        SELECT MAX(we.created_at)
        FROM weather_embeddings we
        WHERE we.document_id = wd.id
          AND we.model_name = %(model_name)s
    )
  )
ORDER BY wd.id
"""

DELETE_EMBEDDINGS_SQL = """
DELETE FROM weather_embeddings
WHERE document_id = %(document_id)s
  AND model_name = %(model_name)s
"""

UPSERT_EMBEDDING_SQL = """
INSERT INTO weather_embeddings (
    id,
    document_id,
    chunk_index,
    chunk_text,
    embedding,
    model_name,
    created_at
) VALUES (
    %(id)s,
    %(document_id)s,
    %(chunk_index)s,
    %(chunk_text)s,
    %(embedding)s,
    %(model_name)s,
    %(created_at)s
)
ON CONFLICT (document_id, chunk_index, model_name) DO UPDATE SET
    chunk_text = EXCLUDED.chunk_text,
    embedding = EXCLUDED.embedding,
    created_at = EXCLUDED.created_at
"""


@dataclass
class DocumentForEmbedding:
    id: str
    narrative_text: str
    synced_at: datetime


@dataclass
class EmbeddingRecord:
    id: str
    document_id: str
    chunk_index: int
    chunk_text: str
    embedding: list[float]
    model_name: str
    created_at: datetime


def get_lakebase_url() -> str:
    """Resolve the database URL from local env or Databricks secrets."""
    load_dotenv()

    url = os.getenv("LAKEBASE_URL")
    if url:
        return url

    scope = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
    key = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")

    try:
        secret = _get_workspace().secrets.get_secret(scope=scope, key=key)
        return base64.b64decode(secret.value).decode("utf-8")
    except Exception as exc:
        raise RuntimeError(
            "LAKEBASE_URL is not configured. Set LAKEBASE_URL in .env for local "
            f"development, or configure the Databricks secret {scope}/{key}."
        ) from exc


def get_connection():
    """Open a PostgreSQL connection to Lakebase."""
    connection = psycopg2.connect(get_lakebase_url())
    register_vector(connection)
    return connection


def _table_exists(table_name: str) -> bool:
    """Check whether a public table exists."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s
                );
                """,
                (table_name,),
            )
            return cursor.fetchone()[0]


def initialize_weather_documents() -> None:
    """Create weather_documents safely if it does not already exist."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(schema_sql)
        connection.commit()


def initialize_weather_embeddings() -> None:
    """Create weather_embeddings safely only on first-time setup."""
    if _table_exists("weather_embeddings"):
        return

    schema_sql = EMBEDDINGS_SCHEMA_PATH.read_text(encoding="utf-8")
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

    if not _table_exists("weather_documents"):
        initialize_weather_documents()

    rows = [_document_to_row(document) for document in documents]

    with get_connection() as connection:
        with connection.cursor() as cursor:
            execute_batch(cursor, UPSERT_SQL, rows, page_size=100)
        connection.commit()

    return len(rows)


def get_documents_needing_embeddings(model_name: str) -> list[DocumentForEmbedding]:
    """Return documents with no embeddings or stale embeddings for a model."""
    with get_connection() as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(DOCUMENTS_NEEDING_EMBEDDINGS_SQL, {"model_name": model_name})
            rows = cursor.fetchall()

    return [
        DocumentForEmbedding(
            id=row["id"],
            narrative_text=row["narrative_text"],
            synced_at=row["synced_at"],
        )
        for row in rows
    ]


def delete_embeddings_for_document(document_id: str, model_name: str) -> int:
    """Remove all embedding rows for one document and model."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                DELETE_EMBEDDINGS_SQL,
                {"document_id": document_id, "model_name": model_name},
            )
            deleted = cursor.rowcount
        connection.commit()

    return deleted


def _embedding_to_row(record: EmbeddingRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "document_id": record.document_id,
        "chunk_index": record.chunk_index,
        "chunk_text": record.chunk_text,
        "embedding": record.embedding,
        "model_name": record.model_name,
        "created_at": record.created_at,
    }


def upsert_weather_embeddings(records: list[EmbeddingRecord]) -> int:
    """Insert or update embedding rows without creating duplicates."""
    if not records:
        return 0

    rows = [_embedding_to_row(record) for record in records]

    with get_connection() as connection:
        with connection.cursor() as cursor:
            execute_batch(cursor, UPSERT_EMBEDDING_SQL, rows, page_size=100)
        connection.commit()

    return len(rows)
