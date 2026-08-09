"""One-time administrative setup for the weather_embeddings HNSW index."""

from __future__ import annotations

import sys

import psycopg2

from lakebase import get_connection

INDEX_NAME = "idx_weather_embeddings_hnsw"
CREATE_HNSW_SQL = """
CREATE INDEX IF NOT EXISTS idx_weather_embeddings_hnsw
ON weather_embeddings
USING hnsw (embedding vector_cosine_ops);
"""

INDEX_EXISTS_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND indexname = %s
);
"""

TABLE_EXISTS_SQL = """
SELECT EXISTS (
    SELECT FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name = 'weather_embeddings'
);
"""


def main() -> None:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(TABLE_EXISTS_SQL)
            if not cursor.fetchone()[0]:
                print(
                    "weather_embeddings table does not exist. "
                    "Run embedding ingestion before creating the HNSW index.",
                    file=sys.stderr,
                )
                sys.exit(1)

            cursor.execute(INDEX_EXISTS_SQL, (INDEX_NAME,))
            if cursor.fetchone()[0]:
                print(f"HNSW index '{INDEX_NAME}' already exists. No action taken.")
                return

            try:
                cursor.execute(CREATE_HNSW_SQL)
                connection.commit()
            except psycopg2.Error as exc:
                connection.rollback()
                print(
                    f"Failed to create HNSW index '{INDEX_NAME}'. "
                    "The connected PostgreSQL role may not own weather_embeddings "
                    "or may lack CREATE privilege on the table.",
                    file=sys.stderr,
                )
                print(f"Database error: {exc}", file=sys.stderr)
                sys.exit(1)

    print(f"HNSW index '{INDEX_NAME}' created successfully.")


if __name__ == "__main__":
    main()
