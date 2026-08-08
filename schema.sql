CREATE TABLE IF NOT EXISTS weather_documents (
    id             TEXT PRIMARY KEY,
    location       TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    headline       TEXT,
    narrative_text TEXT NOT NULL,
    issued_at      TIMESTAMPTZ,
    effective_at   TIMESTAMPTZ,
    payload        JSONB,
    synced_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_weather_documents_location
    ON weather_documents (location);

CREATE INDEX IF NOT EXISTS idx_weather_documents_source_type
    ON weather_documents (source_type);
