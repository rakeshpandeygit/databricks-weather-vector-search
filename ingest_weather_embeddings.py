"""Chunk weather narratives, generate embeddings, and persist them to Lakebase."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from sentence_transformers import SentenceTransformer

from lakebase import (
    DocumentForEmbedding,
    EmbeddingRecord,
    delete_embeddings_for_document,
    get_documents_needing_embeddings,
    initialize_weather_embeddings,
    upsert_weather_embeddings,
)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
CHUNK_STEP = CHUNK_SIZE - CHUNK_OVERLAP
ENCODE_BATCH_SIZE = 32

_model: SentenceTransformer | None = None


def chunk_text(text: str) -> list[str]:
    """Split narrative text into overlapping character-based chunks."""
    normalized = text.strip()
    if not normalized:
        return []

    if len(normalized) <= CHUNK_SIZE:
        return [normalized]

    chunks: list[str] = []
    for start in range(0, len(normalized), CHUNK_STEP):
        chunk = normalized[start : start + CHUNK_SIZE].strip()
        if chunk:
            chunks.append(chunk)
        if start + CHUNK_SIZE >= len(normalized):
            break

    return chunks


def load_model() -> SentenceTransformer:
    """Load the embedding model once per process."""
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(model: SentenceTransformer, texts: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of text chunks."""
    if not texts:
        return []

    vectors = model.encode(texts, batch_size=ENCODE_BATCH_SIZE, show_progress_bar=False)
    embeddings = [vector.tolist() for vector in vectors]

    for index, vector in enumerate(embeddings):
        if len(vector) != EMBEDDING_DIM:
            raise RuntimeError(
                f"Expected embedding dimension {EMBEDDING_DIM}, got {len(vector)} at index {index}"
            )

    return embeddings


def _make_embedding_id(document_id: str, chunk_index: int, model_name: str) -> str:
    return f"{document_id}|{chunk_index}|{model_name}"


def _build_embedding_records(
    document: DocumentForEmbedding,
    chunks: list[str],
    vectors: list[list[float]],
    model_name: str,
    created_at: datetime,
) -> list[EmbeddingRecord]:
    records: list[EmbeddingRecord] = []
    for chunk_index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        records.append(
            EmbeddingRecord(
                id=_make_embedding_id(document.id, chunk_index, model_name),
                document_id=document.id,
                chunk_index=chunk_index,
                chunk_text=chunk,
                embedding=vector,
                model_name=model_name,
                created_at=created_at,
            )
        )
    return records


def run_ingest(model_name: str = MODEL_NAME) -> dict[str, int | str]:
    """Read documents from Lakebase, embed them, and persist vectors."""
    initialize_weather_embeddings()

    documents = get_documents_needing_embeddings(model_name)
    model = load_model()

    documents_processed = 0
    chunks_created = 0
    embeddings_upserted = 0

    for document in documents:
        delete_embeddings_for_document(document.id, model_name)

        chunks = chunk_text(document.narrative_text)
        if not chunks:
            continue

        vectors = embed_texts(model, chunks)
        created_at = datetime.now(timezone.utc)
        records = _build_embedding_records(document, chunks, vectors, model_name, created_at)

        upsert_weather_embeddings(records)

        documents_processed += 1
        chunks_created += len(chunks)
        embeddings_upserted += len(records)

    return {
        "documents_processed": documents_processed,
        "documents_skipped": 0,
        "chunks_created": chunks_created,
        "embeddings_upserted": embeddings_upserted,
        "model_name": model_name,
    }


def run_local_test() -> None:
    """Run embedding tests locally without Lakebase."""
    model = load_model()

    print("=== Test A: single text embedding ===")
    text = "Flash flooding is possible near rivers tonight."
    embedding = embed_texts(model, [text])[0]
    print(f"type: {type(embedding)}")
    print(f"dimension: {len(embedding)}")
    print(f"first 5 values: {embedding[:5]}")

    print("\n=== Test B: chunk + embed sample narrative ===")
    sample_narrative = (
        "A slight chance of showers and thunderstorms after 9pm. Mostly cloudy, "
        "with a low around 72. Southwest wind 5 to 10 mph. Chance of precipitation "
        "is 20%. Conditions may change quickly near rivers and low-lying areas "
        "where ponding water could develop after repeated rounds of rainfall."*12    )
    chunks = chunk_text(sample_narrative)
    vectors = embed_texts(model, chunks)

    print(f"chunk count: {len(chunks)}")
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        print(f"chunk {index}: length={len(chunk)}, vector_dim={len(vector)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate weather narrative embeddings.")
    parser.add_argument(
        "--local-test",
        action="store_true",
        help="Run local embedding tests without connecting to Lakebase.",
    )
    args = parser.parse_args()

    if args.local_test:
        try:
            run_local_test()
        except Exception as exc:
            print(f"Local test failed: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    try:
        summary = run_ingest()
    except Exception as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
