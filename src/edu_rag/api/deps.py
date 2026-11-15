"""Shared dependencies: sessions, the caller's tenant, and the clients."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from edu_rag.clients.llm import OpenRouterClient
from edu_rag.clients.tei import TeiEmbedder, TeiReranker
from edu_rag.db import get_async_session


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_async_session()() as session:
        yield session


def get_owner_id(
    owner_id: Annotated[uuid.UUID, Header(alias="X-Owner-Id")],
) -> uuid.UUID:
    """The tenant the caller is acting for. No auth: the header is trusted."""
    return owner_id


def get_llm(request: Request) -> OpenRouterClient:
    return request.app.state.llm


def get_embedder(request: Request) -> TeiEmbedder:
    return request.app.state.embedder


def get_reranker(request: Request) -> TeiReranker:
    return request.app.state.reranker


SessionDep = Annotated[AsyncSession, Depends(get_session)]
OwnerDep = Annotated[uuid.UUID, Depends(get_owner_id)]
LlmDep = Annotated[OpenRouterClient, Depends(get_llm)]
EmbedderDep = Annotated[TeiEmbedder, Depends(get_embedder)]
RerankerDep = Annotated[TeiReranker, Depends(get_reranker)]
