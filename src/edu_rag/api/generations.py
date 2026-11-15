"""Create a streamed answer, and read one back."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from edu_rag.api.deps import (
    EmbedderDep,
    LlmDep,
    OwnerDep,
    RerankerDep,
    SessionDep,
)
from edu_rag.clients.llm import LlmError, OpenRouterClient, Usage
from edu_rag.clients.tei import TeiEmbedder, TeiReranker
from edu_rag.config import settings
from edu_rag.db import get_async_session
from edu_rag.models import Generation
from edu_rag.retriever import (
    format_context,
    generate_queries,
    resolve_citations,
    search,
)

logger = logging.getLogger(__name__)

_PROMPT = Path(__file__).parent.parent / "prompts" / "generation.md"
_GENERATION_PROMPT = _PROMPT.read_text(encoding="utf-8")

router = APIRouter()


class GenerationCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    document_ids: list[uuid.UUID] | None = None


class GenerationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    status: str
    request: dict[str, Any]
    answer: str
    answer_metadata: dict[str, Any]
    model: str
    usage: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class GenerationSummary(BaseModel):
    """A history row: enough to pick a generation, without the answer."""

    id: uuid.UUID
    status: str
    question: str
    error: str | None
    created_at: datetime
    updated_at: datetime


@router.post("/generations", response_class=StreamingResponse)
async def create_generation(
    body: GenerationCreate,
    owner_id: OwnerDep,
    llm: LlmDep,
    embedder: EmbedderDep,
    reranker: RerankerDep,
) -> StreamingResponse:
    """Stream one answer: ``generation``, then ``delta``s, then ``citations``."""
    async with get_async_session()() as session:
        generation = Generation(
            owner_id=owner_id,
            status="generating",
            request={
                "question": body.question,
                "document_ids": [str(item) for item in body.document_ids or []],
            },
            model=settings.generation_model,
            answer="",
        )
        session.add(generation)
        await session.commit()
        await session.refresh(generation)
        generation_id = generation.id

    return StreamingResponse(
        _generation_stream(
            generation_id,
            body.question,
            body.document_ids,
            owner_id=owner_id,
            llm=llm,
            embedder=embedder,
            reranker=reranker,
        ),
        media_type="text/event-stream",
        headers={
            "X-Generation-Id": str(generation_id),
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/generations", response_model=list[GenerationSummary])
async def list_generations(
    session: SessionDep,
    owner_id: OwnerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[GenerationSummary]:
    stmt = (
        select(Generation)
        .where(Generation.owner_id == owner_id)
        .order_by(Generation.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    generations = (await session.scalars(stmt)).all()
    return [_summary(generation) for generation in generations]


@router.get("/generations/{generation_id}", response_model=GenerationOut)
async def get_generation(
    generation_id: uuid.UUID,
    session: SessionDep,
    owner_id: OwnerDep,
) -> Generation:
    generation = await session.get(Generation, generation_id)
    if generation is None or generation.owner_id != owner_id:
        raise HTTPException(404, "generation not found")
    await _fail_if_stale(session, generation)
    return generation


async def fail_stale() -> None:
    """Fail generations whose stream died with the process that owned it."""
    cutoff = datetime.now(UTC) - timedelta(
        seconds=settings.generation_stale_after_seconds
    )
    async with get_async_session()() as session:
        await session.execute(
            update(Generation)
            .where(Generation.status == "generating", Generation.updated_at < cutoff)
            .values(status="failed", error="stale")
        )
        await session.commit()


async def _fail_if_stale(session: AsyncSession, generation: Generation) -> None:
    """Heal a row whose stream is gone but whose process is still up."""
    if generation.status != "generating":
        return
    cutoff = datetime.now(UTC) - timedelta(
        seconds=settings.generation_stale_after_seconds
    )
    if generation.updated_at >= cutoff:
        return
    await session.execute(
        update(Generation)
        .where(Generation.id == generation.id, Generation.status == "generating")
        .values(status="failed", error="stale")
    )
    await session.commit()
    await session.refresh(generation)


def _summary(generation: Generation) -> GenerationSummary:
    return GenerationSummary(
        id=generation.id,
        status=generation.status,
        question=generation.request.get("question", ""),
        error=generation.error,
        created_at=generation.created_at,
        updated_at=generation.updated_at,
    )


async def _generation_stream(
    generation_id: uuid.UUID,
    question: str,
    document_ids: Sequence[uuid.UUID] | None,
    *,
    owner_id: uuid.UUID,
    llm: OpenRouterClient,
    embedder: TeiEmbedder,
    reranker: TeiReranker,
) -> AsyncIterator[str]:
    yield _sse("generation", {"id": str(generation_id)})

    try:
        queries = await generate_queries(question, llm)
        async with get_async_session()() as session:
            chunks = await search(
                session,
                queries,
                owner_id=owner_id,
                embedder=embedder,
                reranker=reranker,
                document_ids=document_ids,
            )
    except Exception as exc:
        logger.exception("retrieval failed for %s", generation_id)
        await _finish(generation_id, status="failed", error=str(exc))
        yield _sse("error", {"message": str(exc)})
        return

    if not chunks:
        await _finish(generation_id, status="failed", error="no sources")
        yield _sse("error", {"message": "no sources matched the question"})
        return

    messages = [
        {
            "role": "user",
            "content": _GENERATION_PROMPT.format(
                context=format_context(chunks),
                question=question,
            ),
        }
    ]

    answer: list[str] = []
    usage: Usage | None = None
    finish_reason: str | None = None
    try:
        async for chunk in llm.stream_chat(messages):
            if chunk.text:
                answer.append(chunk.text)
                yield _sse("delta", {"text": chunk.text})
            if chunk.usage is not None:
                usage = chunk.usage
            if chunk.finish_reason is not None:
                finish_reason = chunk.finish_reason
    except LlmError as exc:
        logger.warning("generation %s failed: %s", generation_id, exc)
        await _finish(
            generation_id,
            status="failed",
            answer="".join(answer),
            error=str(exc),
        )
        yield _sse("error", {"message": str(exc)})
        return
    except asyncio.CancelledError:
        # The browser is gone. Let the update outlive the cancelled task.
        await asyncio.shield(
            _finish(generation_id, status="cancelled", answer="".join(answer))
        )
        raise

    text = "".join(answer)
    citations = resolve_citations(text, chunks)
    usage_json = usage.model_dump() if usage is not None else None
    await _finish(
        generation_id,
        status="completed",
        answer=text,
        metadata={
            "citations": [citation.model_dump(mode="json") for citation in citations],
            "usage": usage_json,
            "finish_reason": finish_reason,
            "queries": [query.query for query in queries],
        },
        usage=usage_json,
    )
    yield _sse(
        "citations",
        {"citations": [citation.model_dump(mode="json") for citation in citations]},
    )


async def _finish(
    generation_id: uuid.UUID,
    *,
    status: str,
    answer: str | None = None,
    metadata: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    async with get_async_session()() as session:
        generation = await session.get(Generation, generation_id)
        if generation is None:
            return
        generation.status = status
        if answer is not None:
            generation.answer = answer
        if metadata is not None:
            generation.answer_metadata = metadata
        if usage is not None:
            generation.usage = usage
        if error is not None:
            generation.error = error
        await session.commit()


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
