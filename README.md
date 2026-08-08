# Weather Intelligence Retrieval Service

## Project Overview

This project ingests unstructured weather narrative text from the National Weather Service (NWS), normalizes it into a stable document format, and stores it in Databricks Lakebase (PostgreSQL). A separate embedding pipeline chunks those narratives and stores 384-dimensional vectors in a pgvector table. Semantic search over those vectors is planned for Milestone 3.

Supported locations: `Chicago, IL` and `Austin, TX`.

## Architecture

```text
NWS API
  -> normalization (weather_client.py)
  -> weather_documents (Lakebase)
  -> chunking (800 chars, 100 overlap)
  -> MiniLM embeddings (384-dim)
  -> weather_embeddings / pgvector
  -> Milestone 3 semantic search (not implemented)
```

## How It Works

### A. Weather ingestion

For each requested location, the service resolves grid coordinates, fetches multi-day forecasts and point-specific active alerts from `api.weather.gov`, and normalizes the results into `WeatherDocument` records. The Flask app exposes this as `POST /api/sync`, which upserts documents into `weather_documents`.

### B. Embedding pipeline

`ingest_weather_embeddings.py` reads documents that need embeddings, splits `narrative_text` into overlapping character-based chunks (`CHUNK_SIZE=800`, `CHUNK_OVERLAP=100`), encodes each chunk with `sentence-transformers/all-MiniLM-L6-v2`, and writes rows to `weather_embeddings`. The model produces 384-dimensional vectors stored via the pgvector extension.

pgvector adds a native `vector(384)` column type to PostgreSQL so embeddings can be compared with vector operators. HNSW indexing and cosine search are deferred to Milestone 3.

### C. Incremental refresh / idempotency

**Document sync:** Records are upserted by stable document `id`. Re-running sync does not create duplicate rows.

**`synced_at`:** This timestamp reflects when meaningful narrative content last changed — not every sync attempt. If `narrative_text` is unchanged, the existing row (including `synced_at`) is left as-is. New or changed narratives receive an updated `synced_at`.

**Embedding ingest:** A document is processed when it has no embeddings for the model, or when `weather_documents.synced_at` is later than the latest `weather_embeddings.created_at` for that document. Stale embeddings are deleted and rebuilt from the current narrative. Re-running ingest with no source changes processes zero documents.

Schema initialization is non-destructive and only occurs when the corresponding table does not exist.

## Data Model

**`weather_documents`** — One row per normalized forecast period or active alert. Key fields: stable `id`, `location`, `source_type` (`forecast` or `alert`), `headline`, `narrative_text`, and `synced_at`.

**`weather_embeddings`** — One or more rows per document chunk. Key fields: `document_id` (references `weather_documents.id`), `chunk_index`, `chunk_text`, `embedding vector(384)`, and `model_name`. Uniqueness is enforced on `(document_id, chunk_index, model_name)`.

## Run Locally

```powershell
cd path\to\databricks-weather-vector-search
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Required environment variables:

```env
NWS_USER_AGENT=weather-vector-search/1.0 your-email@example.com
LAKEBASE_URL=postgresql://user:password@host:5432/dbname
```

**Test NWS harvesting (no database):**

```powershell
python weather_client.py
```

**Run the Flask app:**

```powershell
python app.py
```

**Sync weather documents:**

```http
POST http://localhost:5000/api/sync
Content-Type: application/json

{
  "locations": ["Chicago, IL", "Austin, TX"],
  "limit": 50
}
```

**Test embeddings locally (no database):**

```powershell
python ingest_weather_embeddings.py --local-test
```

**Ingest embeddings to Lakebase** (after sync has populated `weather_documents`):

```powershell
python ingest_weather_embeddings.py
```

The first embedding run downloads the MiniLM model (~80 MB).

## Databricks Deployment

The Flask app is deployed via `app.yaml`. The Lakebase connection URL is resolved from Databricks secrets (`database` scope, `lakebase-url` key). `NWS_USER_AGENT` is provided as app environment configuration. Embedding ingestion is currently a standalone script, not part of the Flask app.

## Verification

```sql
-- Embedding row count
SELECT COUNT(*) FROM weather_embeddings;

-- Vector dimension (should always be 384)
SELECT document_id, chunk_index, vector_dims(embedding) AS dims
FROM weather_embeddings
LIMIT 5;

-- Source freshness vs embeddings
SELECT wd.id, wd.synced_at, MAX(we.created_at) AS latest_embedding_at
FROM weather_documents wd
LEFT JOIN weather_embeddings we
  ON we.document_id = wd.id
 AND we.model_name = 'sentence-transformers/all-MiniLM-L6-v2'
GROUP BY wd.id, wd.synced_at
ORDER BY wd.id;
```

Documents where `synced_at <= latest_embedding_at` should be skipped on the next ingest run.

## Project Structure

| File | Role |
|---|---|
| `weather_client.py` | NWS API calls and normalization |
| `lakebase.py` | Lakebase connection and persistence |
| `app.py` | Flask API (`POST /api/sync`) |
| `ingest_weather_embeddings.py` | Chunking and embedding pipeline |
| `schema.sql` | `weather_documents` DDL |
| `schema_embeddings.sql` | `weather_embeddings` DDL |
| `app.yaml` | Databricks App config |
| `requirements.txt` | Python dependencies |

## Current Status

| Milestone | Scope | Status |
|---|---|---|
| Milestone 1 | Weather ingestion | COMPLETE |
| Milestone 2 | Chunking + embeddings | COMPLETE |
| Milestone 3 | Semantic search | NEXT |
