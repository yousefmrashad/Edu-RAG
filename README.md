# 📚 Edu-RAG

<p align="center">
  <img src="https://img.shields.io/badge/python-3.13%2B-blue.svg" alt="Python: 3.13+" />
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL" />
  <img src="https://img.shields.io/badge/Celery-37814A?logo=celery&logoColor=white" alt="Celery" />
  <img src="https://img.shields.io/badge/RabbitMQ-FF6600?logo=rabbitmq&logoColor=white" alt="RabbitMQ" />
  <img src="https://img.shields.io/badge/Hugging_Face-000000?logo=huggingface&logoColor=FFD21E" alt="TEI" />
  <img src="https://img.shields.io/badge/License-GPLv3-orange.svg" alt="License: GPL v3" />
</p>

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
```
