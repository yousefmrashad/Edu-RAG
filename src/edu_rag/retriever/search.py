"""Hybrid retrieval over pgvector.

One query is embedded, searched twice (vector and full-text), and fused with
RRF. The candidate set is then expanded to whole chunk groups when most of a
group is already present, and the expanded set is reranked. Several queries
run in sequence and their results are unioned by chunk id.

The expansion is the reason there is no parent-chunk table: a group is
``ordinal // 4`` or ``ordinal // 16``, so the parent is derived at query time.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from edu_rag.clients.tei import TeiEmbedder, TeiReranker
from edu_rag.config import settings
from edu_rag.models import Chunk, Document
from edu_rag.retriever.queries import GeneratedQuery

# The chunker's groups: 4 chunks are roughly a section, 16 a larger unit.
L1 = 4
L2 = 16


@dataclass(frozen=True)
class Hit:
    """A chunk and the fused score that put it in the candidate set."""

    chunk: Chunk
    score: float


async def search(
    session: AsyncSession,
    queries: Sequence[GeneratedQuery],
    *,
    owner_id: uuid.UUID,
    embedder: TeiEmbedder,
    reranker: TeiReranker,
) -> list[Chunk]:
    """Run every query, expand, rerank, and return the deduplicated union."""
    vectors = await asyncio.gather(
        *(embedder.embed_query(q.query, instruction=q.instruction) for q in queries)
    )

    merged: dict[uuid.UUID, Chunk] = {}
    for query, vector in zip(queries, vectors, strict=True):
        hits = await _hybrid(session, query.query, vector, owner_id)
        expanded = await _auto_merge(session, hits)
        for chunk in await _rerank(reranker, query.query, expanded):
            merged.setdefault(chunk.id, chunk)
    return list(merged.values())


async def _hybrid(
    session: AsyncSession,
    query: str,
    vector: list[float],
    owner_id: uuid.UUID,
) -> list[Hit]:
    """Vector + full-text candidates, fused by reciprocal rank."""
    tsquery = func.plainto_tsquery("english", query)
    distance = Chunk.embedding.cosine_distance(vector)
    rank = func.ts_rank_cd(Chunk.tsv, tsquery).desc()

    vec = (
        select(
            Chunk.id,
            func.row_number().over(order_by=distance).label("r"),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.owner_id == owner_id, Document.status == "ready")
        .order_by(distance)
        .limit(settings.retrieve_candidates)
        .cte("vec")
    )
    kw = (
        select(
            Chunk.id,
            func.row_number().over(order_by=rank).label("r"),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Document.owner_id == owner_id,
            Document.status == "ready",
            Chunk.tsv.op("@@")(tsquery),
        )
        .order_by(rank)
        .limit(settings.retrieve_candidates)
        .cte("kw")
    )
    fused = union_all(
        select(vec.c.id, vec.c.r),
        select(kw.c.id, kw.c.r),
    ).subquery()
    score = func.sum(1.0 / (settings.rrf_k + fused.c.r)).label("score")
    stmt = (
        select(fused.c.id, score)
        .group_by(fused.c.id)
        .order_by(score.desc())
        .limit(settings.retrieve_candidates)
    )

    rows = await session.execute(stmt)
    scores = {row.id: float(row.score) for row in rows}
    if not scores:
        return []

    chunks = (
        (await session.execute(select(Chunk).where(Chunk.id.in_(scores))))
        .scalars()
        .all()
    )
    by_id = {chunk.id: chunk for chunk in chunks}
    return [
        Hit(chunk=by_id[chunk_id], score=score)
        for chunk_id, score in scores.items()
        if chunk_id in by_id
    ]


async def _auto_merge(session: AsyncSession, hits: Sequence[Hit]) -> list[Chunk]:
    """Pull in whole groups when most of a group is already retrieved."""
    merged: dict[uuid.UUID, Chunk] = {hit.chunk.id: hit.chunk for hit in hits}

    l1_hits = Counter((hit.chunk.document_id, hit.chunk.ordinal // L1) for hit in hits)
    l2_hits = Counter((hit.chunk.document_id, hit.chunk.ordinal // L2) for hit in hits)

    l1_groups = {key for key, count in l1_hits.items() if count > L1 // 2}
    l2_groups = {key for key, count in l2_hits.items() if count > L2 // 2}

    # A level-1 group inside a level-2 group is already covered.
    l1_groups = {
        key for key in l1_groups if (key[0], key[1] // (L2 // L1)) not in l2_groups
    }

    for document_id, group in l1_groups:
        for chunk in await _fetch_range(
            session, document_id, group * L1, (group + 1) * L1
        ):
            merged[chunk.id] = chunk

    for document_id, group in l2_groups:
        for chunk in await _fetch_range(
            session, document_id, group * L2, (group + 1) * L2
        ):
            merged[chunk.id] = chunk

    return list(merged.values())


async def _fetch_range(
    session: AsyncSession,
    document_id: uuid.UUID,
    start: int,
    end: int,
) -> list[Chunk]:
    result = await session.execute(
        select(Chunk)
        .where(
            Chunk.document_id == document_id,
            Chunk.ordinal >= start,
            Chunk.ordinal < end,
        )
        .order_by(Chunk.ordinal)
    )
    return list(result.scalars().all())


async def _rerank(
    reranker: TeiReranker,
    query: str,
    chunks: Sequence[Chunk],
) -> list[Chunk]:
    if not chunks:
        return []
    ranking = await reranker.rerank(
        query,
        [chunk.text for chunk in chunks],
        top_k=settings.context_chunks,
    )
    return [chunks[index] for index, _ in ranking]
