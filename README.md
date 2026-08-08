# Weather Intelligence Retrieval Service

Milestone 1 harvests unstructured weather narrative text from the National Weather Service (NWS), normalizes it into a stable internal `WeatherDocument` structure, and upserts records into Lakebase/PostgreSQL.

Embedding generation, pgvector storage, semantic search, and `POST /weather/search` are intentionally deferred to later milestones.

## Milestone 1 Architecture

```text
NWS API
   ↓
weather_client.py
   ↓
normalize raw NWS JSON
   ↓
WeatherDocument
   ↓
lakebase.py
   ↓
weather_documents table
   ↓
POST /weather/sync
```

For each supported location, the service:

1. Calls `GET /points/{lat},{lon}` to discover the forecast URL.
2. Calls the returned forecast URL and normalizes each useful `properties.periods[]` item.
3. Calls `GET /alerts/active?point={lat},{lon}` for point-specific active alerts.
4. Normalizes each useful alert feature into the same `WeatherDocument` contract.
5. Upserts all records into `weather_documents`.

## Why Normalization Is Needed

The NWS API returns nested GeoJSON with different shapes for forecasts and alerts. The rest of the system should not depend on those raw response structures. Normalization converts external JSON into one predictable internal record shape that later milestones can chunk, embed, and search.

## NWS Endpoints Used

Base URL: `https://api.weather.gov`

Forecast flow:

- `GET /points/{lat},{lon}`
- follow `properties.forecast`
- read `properties.periods[]`

Alert flow:

- `GET /alerts/active?point={lat},{lon}`
- read `features[]`

Important free-text fields:

- forecast: `detailedForecast`
- alerts: `description`, `instruction`

## WeatherDocument Contract

One lightweight dataclass in `weather_client.py`:

- `id`
- `location`
- `source_type` (`forecast` or `alert`)
- `headline`
- `narrative_text`
- `issued_at`
- `effective_at`
- `payload`
- `synced_at`

Forecast IDs are deterministic, for example:

```text
Chicago, IL|2026-08-08T18:00:00-05:00
```

Alert IDs prefer the top-level GeoJSON feature `id`.

## Supported Locations

- `Chicago, IL` → `41.8781`, `-87.6298`
- `Austin, TX` → `30.2672`, `-97.7431`

No geocoding API is used in Milestone 1.

## Local Setup

```powershell
cd "path\to\databricks-weather-vector-search"
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set:

```env
NWS_USER_AGENT=weather-vector-search/1.0 your-email@example.com
```

For database-backed sync testing, also set:

```env
LAKEBASE_URL=postgresql://user:password@host:5432/dbname
```

## Local API Test (No Lakebase Required)

```powershell
python weather_client.py
```

This will:

1. fetch Chicago point metadata
2. fetch Chicago multi-day forecast
3. fetch Chicago point-specific active alerts
4. normalize the results
5. print 2-3 readable `WeatherDocument` examples

You should see the flow:

```text
external JSON → normalization → WeatherDocument
```

## Lakebase Configuration

Local development:

- set `LAKEBASE_URL` in `.env`
- set `NWS_USER_AGENT` in `.env`

Databricks App:

- `app.yaml` sets Lakebase scope/key env vars (same pattern as the Day 2 Lakebase assignment)
- Lakebase URL is read from secret scope `database`, key `lakebase-url`
- set `NWS_USER_AGENT` in the Databricks App environment configuration (not a secret — NWS has no API key)

Database initialization is non-destructive and uses:

- `CREATE TABLE IF NOT EXISTS`
- `CREATE INDEX IF NOT EXISTS`

It never runs `DROP TABLE`, `TRUNCATE TABLE`, or bulk deletes.

## Run the Flask App

```powershell
python app.py
```

Health check:

```http
GET http://localhost:5000/health
```

Sync example:

```http
POST http://localhost:5000/weather/sync
Content-Type: application/json

{
  "locations": ["Chicago, IL", "Austin, TX"],
  "limit": 50
}
```

Example response:

```json
{
  "locations": ["Chicago, IL", "Austin, TX"],
  "documents_synced": 25,
  "source_counts": {
    "forecast": 24,
    "alert": 1
  }
}
```

If one location fails and another succeeds, the response uses partial-success behavior and may include:

```json
"errors": [
  {
    "location": "Austin, TX",
    "message": "..."
  }
]
```

## Upsert / Idempotency

Records are written with:

```sql
INSERT ... ON CONFLICT (id) DO UPDATE
```

Re-running the same sync should keep approximately the same number of rows while refreshing mutable fields such as `headline`, `narrative_text`, timestamps, and `payload`.

## Current Limitations

- Only `Chicago, IL` and `Austin, TX` are supported.
- Hourly forecast is not ingested in Milestone 1.
- No embeddings, pgvector columns, or semantic search yet.
- `ingest_weather_embeddings.py` is a placeholder for Milestone 2.
- `POST /weather/search` is not implemented yet.

## Project Files

- `weather_client.py` — NWS harvesting and normalization
- `lakebase.py` — PostgreSQL/Lakebase connection and upsert
- `app.py` — Flask API with `POST /weather/sync`
- `ingest_weather_embeddings.py` — Milestone 2 placeholder
- `schema.sql` — `weather_documents` DDL
- `app.yaml` — Databricks App startup config
- `requirements.txt` — Milestone 1 dependencies
