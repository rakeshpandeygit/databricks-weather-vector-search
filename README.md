# Weather Intelligence Retrieval Service

## Project Overview

This project ingests unstructured weather narrative text from the National Weather Service (NWS), normalizes it into a stable document format, and stores it in Databricks Lakebase (PostgreSQL). An embedding pipeline chunks those narratives and stores 384-dimensional vectors in a pgvector table. Semantic retrieval ranks the closest chunks by cosine distance.

Supported locations: `Chicago, IL` and `Austin, TX`.

## Architecture

```text
NWS API
  -> normalization (weather_client.py)
  -> weather_documents (Lakebase)
  -> chunking (800 chars, 100 overlap)
  -> MiniLM embeddings (384-dim)
  -> weather_embeddings / pgvector
  -> POST /weather/search (cosine retrieval)
```

## How It Works

### Weather ingestion

Forecasts and alerts are normalized into the same `WeatherDocument` schema with `source_type` set to `forecast` or `alert`. Upsert via `POST /weather/sync`.

### Embedding pipeline

Documents are chunked and encoded with `sentence-transformers/all-MiniLM-L6-v2`. Run via `python ingest_weather_embeddings.py` or `POST /api/embeddings/ingest`. Embeddings rebuild only when missing or older than the source document's `synced_at`.

### Semantic search

`POST /weather/search` embeds the query with the same MiniLM model, then ranks chunks using pgvector cosine distance (`<=>`). Smaller `distance` means a closer match. `similarity` is `1.0 - distance`. Search can return all source types or filter to `alert` or `forecast`. Retrieval only — no LLM answer generation.

## API Endpoints

| Required route | Databricks alias | Purpose |
|---|---|---|
| `POST /weather/sync` | `POST /api/sync` | Sync NWS documents |
| `POST /weather/search` | `POST /api/search` | Semantic retrieval |

Additional: `POST /api/embeddings/ingest` (embedding trigger for Databricks)

**Sync example:**

```http
POST http://localhost:5000/weather/sync
Content-Type: application/json

{"locations": ["Chicago, IL", "Austin, TX"]}
```

**Search example (all sources):**

```http
POST http://localhost:5000/weather/search
Content-Type: application/json

{
  "query": "risk of flooding near rivers",
  "top_k": 5
}
```

**Search with source filter:**

```http
POST http://localhost:5000/weather/search
Content-Type: application/json

{
  "query": "severe thunderstorms",
  "top_k": 5,
  "source_type": "alert"
}
```

Allowed `source_type` values: `alert`, `forecast`. Invalid values return HTTP 400.

## Run Locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

```env
NWS_USER_AGENT=weather-vector-search/1.0 your-email@example.com
LAKEBASE_URL=postgresql://user:password@host:5432/dbname
```

```powershell
python weather_client.py
python app.py
python ingest_weather_embeddings.py --local-test
python ingest_weather_embeddings.py
```

## HNSW Index (Optional)

Exact cosine search works without an ANN index. HNSW is an approximate-nearest-neighbor optimization for larger datasets.

The index is **not** created automatically during sync, search, or embedding ingest because repeated `CREATE INDEX` can fail when the connected role does not own the table.

Create it once explicitly:

```powershell
python setup_vector_index.py
```

Index SQL:

```sql
CREATE INDEX IF NOT EXISTS idx_weather_embeddings_hnsw
ON weather_embeddings
USING hnsw (embedding vector_cosine_ops);
```

## Query Plan Benchmark

Compare plans before and after creating the HNSW index using `EXPLAIN ANALYZE` in Lakebase. Replace the vector literal with any valid 384-dimensional query vector.

**State A — before HNSW:**

```sql
EXPLAIN ANALYZE
SELECT we.document_id, we.chunk_text,
       we.embedding <=> '[0.01,0.02,...]'::vector AS distance
FROM weather_embeddings we
JOIN weather_documents wd ON wd.id = we.document_id
WHERE we.model_name = 'sentence-transformers/all-MiniLM-L6-v2'
ORDER BY we.embedding <=> '[0.01,0.02,...]'::vector
LIMIT 5;
```

**State B — after HNSW:** run the same query again.

With only tens of vectors, PostgreSQL may still choose a sequential scan and latency differences may be negligible. The benchmark demonstrates index setup and plan comparison — not performance gains on this tiny dataset. Do not force planner settings to manufacture a faster result.

## Databricks Deployment

Deploy via `app.yaml`. Lakebase URL comes from Databricks secrets (`database` / `lakebase-url`). `NWS_USER_AGENT` is set in app environment configuration.

## Project Structure

| File | Role |
|---|---|
| `weather_client.py` | NWS API calls and normalization |
| `lakebase.py` | Lakebase connection, persistence, vector search |
| `app.py` | Flask API |
| `ingest_weather_embeddings.py` | Chunking and embedding pipeline |
| `setup_vector_index.py` | One-time HNSW index setup |
| `schema.sql` / `schema_embeddings.sql` | Table DDL |
| `app.yaml` | Databricks App config |

## Current Status

| Milestone | Scope | Status |
|---|---|---|
| Milestone 1 | Weather ingestion | COMPLETE |
| Milestone 2 | Chunking + embeddings | COMPLETE |
| Milestone 3 | Semantic search + source filter | COMPLETE |

## Known Limitations

- Only `Chicago, IL` and `Austin, TX` are supported.
- Search returns retrieved chunks only — no generated answer.
- `POST /api/sync` `limit` may exclude alerts when set too low.


## Assignment Deliverable Notes

### Data source choice

The project uses the National Weather Service (NWS) API because it is free,
requires no API key, and provides useful unstructured narrative text for both
weather forecasts and active alerts. This makes it a good source for testing
normalization, embeddings, and semantic retrieval without adding external
authentication complexity.

### Schema and embedding decisions

Weather data is normalized into `weather_documents`, with key fields including:

- `id` — stable deduplication key
- `location`
- `source_type` — `forecast` or `alert`
- `headline`
- `narrative_text`
- `issued_at`
- `effective_at`
- `payload`
- `synced_at`

Embeddings are stored separately in `weather_embeddings`, with one row per
text chunk. Important fields include:

- `document_id`
- `chunk_index`
- `chunk_text`
- `embedding vector(384)`
- `model_name`
- `created_at`

Narrative text is chunked using:

- chunk size: `800` characters
- overlap: `100` characters

Embeddings are generated with:

`sentence-transformers/all-MiniLM-L6-v2`

which produces `384`-dimensional vectors.

### End-to-end pipeline

The complete flow is:

```text
POST /weather/sync
        ↓
NWS forecasts + active alerts
        ↓
normalize to WeatherDocument
        ↓
weather_documents
        ↓
embedding ingestion
        ↓
chunk narrative text
        ↓
all-MiniLM-L6-v2
        ↓
weather_embeddings / pgvector
        ↓
POST /weather/search
        ↓
query embedding
        ↓
cosine similarity search
        ↓
top-k matching weather chunks
```

#### Typical Execution order
Typical execution order:

1. Call POST `/weather/sync` to populate or refresh `weather_documents`.
2. Run python `ingest_weather_embeddings.py` or call `POST /api/embeddings/ingest`.
3. Call `POST /weather/search` with a natural-language query.
5. Optionally filter retrieval with `source_type = alert` or `forecast`.

### Known limitations and future improvements
Curren limitations include -
 - Only explicitly configured locations are supported.
 - `limit` on weather sync is applied after forecast and alert records are
combined, so a small value may allow forecast records to consume the limit
before active alerts are included.
 - Search performs retrieval only; it does not generate an LLM-based answer.
 - Embedding ingestion was triggered through the deployed Databricks App during
Free Edition integration testing because the interactive workspace runtime
encountered dependency/runtime issues with `sentence-transformers`.
 - The current dataset is very small, so an HNSW index is not expected to show
meaningful performance gains.

Given more time, improvements would include dynamic location resolution,
scheduled weather refresh, improved sync limit semantics, larger-scale HNSW
benchmarking, and optional RAG-based natural-language answers.
