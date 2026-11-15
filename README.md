<h1 align="center">📚 Edu-RAG</h1>
<p align="center">
  <img src="https://img.shields.io/badge/python-3.13%2B-blue.svg" alt="Python: 3.13+" />
  <img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/pgvector-4169E1?logo=postgresql&logoColor=white" alt="pgvector" />
  <img src="https://img.shields.io/badge/TEI-000000?logo=huggingface&logoColor=FFD21E" alt="TEI" />
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

You need Docker with the Compose plugin, and an NVIDIA GPU with the container
toolkit: both TEI containers request `gpus: all` and run a CUDA image. Together
they use a few GB of VRAM. Pick the image tag that matches your card — or
`cpu-latest` to run without a GPU — from the
[TEI supported-hardware table](https://huggingface.co/docs/text-embeddings-inference/supported_models).

```bash
cp .env.example .env
# fill in OPENROUTER_API_KEY and GENERATION_MODEL
docker compose up -d postgres rabbitmq
docker compose run --rm api alembic upgrade head   # first run only
docker compose up -d
```

The last step starts Postgres, RabbitMQ, both TEI containers, the API, and the
ingest worker. The API listens on `http://localhost:8000`; RabbitMQ's
management UI is on `http://localhost:15672` (`edu` / `edu`). [API](#api) has a
request that goes from upload to streamed answer.

The migration creates the `vector` extension and the three tables, so
`alembic upgrade head` is the only schema step.

## Local development

Run the infrastructure in Docker and the app on the host:

```bash
docker compose up -d postgres rabbitmq tei-embedder tei-reranker
uv sync --extra ingest
alembic upgrade head
uvicorn edu_rag.api:app --reload
celery -A edu_rag.celery_app:app worker -Q ingest --concurrency=1
```

`--extra ingest` is what the worker needs (docling, Paddle); the API itself
only needs the core dependencies. `uvicorn` blocks, so start the Celery
command in a second terminal. Both processes read `.env`, which points at
`localhost`.

## Configuration

Copy `.env.example` to `.env`. The values that matter:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://...` |
| `CELERY_BROKER_URL` | RabbitMQ |
| `EMBEDDER_URL` / `RERANKER_URL` | TEI containers |
| `OPENROUTER_API_KEY` | generation |
| `GENERATION_MODEL` | OpenRouter model id |
| `LLM_REQUESTS_PER_MINUTE` | local request shaping (default 30) |
| `RETRIEVE_QUERIES` | queries per question (default 6) |
| `RETRIEVE_CANDIDATES` | candidates per retriever (default 50) |
| `RETRIEVE_AUTO_MERGE` | expand to whole chunk groups (default true) |
| `RRF_K` | RRF constant (default 30) |
| `CONTEXT_CHUNKS` | chunks per query after reranking (default 15) |
| `MAX_UPLOAD_BYTES` | upload cap (default 100 MiB) |
| `GENERATION_STALE_AFTER_SECONDS` | cutoff for dead generations (default 900) |

## API

Every route except `/health` requires `X-Owner-Id: <uuid>`. There is no local
auth: the caller is trusted and the header selects the tenant.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/documents` | upload (`file`, optional `title`), queues ingest |
| `GET` | `/documents` | list, newest first |
| `GET` | `/documents/{id}` | one document |
| `POST` | `/generations` | stream one answer |
| `GET` | `/generations` | list, newest first |
| `GET` | `/generations/{id}` | read a finished answer |
| `GET` | `/health` | liveness |

### End to end

`lesson.pdf` is any file docling handles — PDF, DOCX, HTML, Markdown, images.

```bash
OWNER=$(python -c "import uuid; print(uuid.uuid4())")

# 1. Upload. 201 with the document id, or 200 if the same bytes already exist.
curl -s -X POST http://localhost:8000/documents \
  -H "X-Owner-Id: $OWNER" \
  -F "file=@lesson.pdf" \
  -F "title=Lesson 1"

# 2. Poll until status is "ready" (uploaded -> processing -> ready).
curl -s http://localhost:8000/documents/<document-id> -H "X-Owner-Id: $OWNER"

# 3. Ask. -N disables curl's output buffering, which SSE needs.
curl -N -X POST http://localhost:8000/generations \
  -H "X-Owner-Id: $OWNER" \
  -H "Content-Type: application/json" \
  -d '{"question": "What is phishing?"}'
```

The response to step 3 is `text/event-stream` and carries `X-Generation-Id` in
its headers (`curl -D -` prints them):

```
event: generation
data: {"id": "9b1e..."}

event: delta
data: {"text": "Phishing is"}

event: delta
data: {"text": " a social-engineering attack."}

event: citations
data: {"citations": [{"index": 1, "chunk_id": "...", "document_id": "...", "ordinal": 4, "headings": ["Phishing"], "page_start": 2, "page_end": 2, "text": "..."}]}
```

The events are `generation` (the id), `delta` (answer text), and `citations`
(last). A failure ends the stream with `error` instead; the row is marked
`failed` and `GET /generations/{id}` returns it.

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
