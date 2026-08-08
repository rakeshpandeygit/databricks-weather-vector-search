CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS weather_embeddings (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL
        REFERENCES weather_documents(id) ON DELETE CASCADE,
    chunk_index   INTEGER NOT NULL,
    chunk_text    TEXT NOT NULL,
    embedding     vector(384) NOT NULL,
    model_name    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    UNIQUE (document_id, chunk_index, model_name)
);

CREATE INDEX IF NOT EXISTS idx_weather_embeddings_document_id
    ON weather_embeddings (document_id);

CREATE INDEX IF NOT EXISTS idx_weather_embeddings_model_name
    ON weather_embeddings (model_name);
