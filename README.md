# Edu-RAG

A retrieval-augmented generation service for educational content. It ingests
documents, chunks and embeds them, retrieves the passages that answer a question,
and streams a cited answer.

## How it works

**Ingest** — a Celery worker parses an uploaded document with docling, chunks it
with `HybridChunker`, embeds the chunks with the TEI embedder, and writes them to
Postgres. The document row is the queue: `uploaded` → `processing` → `ready`.

**Retrieval** — the API asks the language model for a small set of search
queries, embeds each with its own instruction, and runs vector + full-text
search. The two ranked lists are fused with reciprocal rank fusion (RRF). When
most of a 4- or 16-chunk group is already retrieved, the whole group is pulled
in, and the expanded set is reranked per query.

**Generation** — the fused chunks are numbered and inserted into the prompt. The
answer streams over SSE; a final event carries the citations resolved from the
`[n]` markers.

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI, Server-Sent Events |
| Storage | PostgreSQL 18 + pgvector |
| Queue | Celery + RabbitMQ |
| Parsing | docling + RapidOCR (Paddle, PP-OCRv6) |
| Embeddings | `microsoft/harrier-oss-v1-0.6b` via TEI |
| Reranking | `mixedbread-ai/mxbai-rerank-base-v2` via TEI |
| Generation | OpenRouter |

## Quick start

```bash
cp .env.example .env
# fill in OPENROUTER_API_KEY and GENERATION_MODEL
uv sync
docker compose up -d
```

The API will listen on `http://localhost:8000`; RabbitMQ's management UI is on
`http://localhost:15672` (`edu` / `edu`).

Run the two processes locally:

```bash
uvicorn edu_rag.api:app --reload
celery -A edu_rag.celery_app:app worker -Q ingest --concurrency=1
```

Apply the schema once the initial migration exists:

```bash
alembic upgrade head
```

## Configuration

Copy `.env.example` to `.env`. The values that matter:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://...` |
| `CELERY_BROKER_URL` | RabbitMQ |
| `EMBEDDER_URL` / `RERANKER_URL` | TEI containers |
| `OPENROUTER_API_KEY` | generation |
| `GENERATION_MODEL` | OpenRouter model id |
| `RETRIEVE_QUERIES` | queries per question (default 6) |
| `RETRIEVE_CANDIDATES` | candidates per retriever (default 50) |
| `RRF_K` | RRF constant (default 30) |
| `CONTEXT_CHUNKS` | chunks per query after reranking (default 6) |

## Project layout

```
src/edu_rag/
  api/            # FastAPI app, SSE generation
  clients/        # TEI and OpenRouter clients
  prompts/        # query generation and answer prompts
  retriever/      # query generation, hybrid search, citations
  workers/        # Celery ingest worker
  celery_app.py
  config.py
  db.py
  models.py
  tokenizer.py
migrations/       # Alembic
notes/            # design decisions
```

## Status

- Ingest, retrieval, and the generation prompt are implemented.
- The API and the initial Alembic migration are the next pieces.
- `tests/` is empty; the retriever has only been verified against a live
  database for SQL compilation, not yet end to end.

## Design notes

- [`notes/retrieval-fusion.md`](notes/retrieval-fusion.md) — why RRF over
  pgvector, and what Postgres FTS costs us.
