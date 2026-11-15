"""HTTP surface: uploads, retrieval, and the streamed answer.

The API is a thin layer over the retriever. It owns the request lifecycle, the
persisted rows, and the SSE framing; retrieval and generation live in their own
modules so the worker and the API can share them.

Generation is the one place that does not use a request-scoped session: a
FastAPI yield dependency closes when the handler returns, which is before the
stream is consumed. Every database touch opens its own short-lived session
instead.

There is no authentication and no users table. The caller identifies the tenant
with an ``X-Owner-Id`` header, and every row is scoped to it.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from edu_rag.api import documents, generations
from edu_rag.clients.llm import OpenRouterClient, TokenBucket
from edu_rag.clients.tei import TeiEmbedder, TeiReranker
from edu_rag.config import settings
from edu_rag.db import get_async_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Build the long-lived clients once per process."""
    limiter = TokenBucket(
        capacity=settings.llm_requests_per_minute,
        refill_per_second=settings.llm_requests_per_minute / 60,
    )
    llm = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        model=settings.generation_model,
        limiter=limiter,
    )
    embedder = TeiEmbedder(
        settings.embedder_url,
        embedding_dim=settings.embedding_dim,
        timeout=settings.http_timeout,
    )
    reranker = TeiReranker(settings.reranker_url, timeout=settings.http_timeout)

    app.state.llm = llm
    app.state.embedder = embedder
    app.state.reranker = reranker
    await generations.fail_stale()

    try:
        yield
    finally:
        await llm.aclose()
        await embedder.aclose()
        await reranker.aclose()
        await get_async_engine().dispose()


app = FastAPI(title="Edu-RAG", lifespan=lifespan)
app.include_router(documents.router)
app.include_router(generations.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


__all__ = ["app"]
