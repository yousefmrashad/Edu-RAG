"""Resolve ``[n]`` citation markers in a generated answer to retrieved chunks.

The context is numbered 1..n in the order the chunks are given to the
generator. This module reads the same markers back, so the API can send a
citation list alongside the answer without parsing prose itself.

Only the markers the answer actually contains are returned, in first-citation
order, deduplicated. Out-of-range numbers are dropped: the model occasionally
invents one, and a bad citation should not fail the response.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Sequence

from pydantic import BaseModel

from edu_rag.models import Chunk

logger = logging.getLogger(__name__)

# [1], [1, 2], [1,2]. The prompt asks for [1][2], but models mix the styles.
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

# A fenced block may contain bracket expressions that are not citations.
_FENCE = re.compile(r"```.*?```", re.DOTALL)


class Citation(BaseModel):
    """One source the answer cited, with enough data to render a card."""

    index: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    headings: list[str]
    page_start: int | None
    page_end: int | None
    text: str


def resolve_citations(answer: str, chunks: Sequence[Chunk]) -> list[Citation]:
    """Return the chunks the answer cites, in the order they are first cited.

    ``chunks`` must be the same sequence, in the same order, that produced the
    ``1..n`` numbering in the prompt's context.
    """
    citations: list[Citation] = []
    seen: set[int] = set()
    for match in _CITATION.finditer(_FENCE.sub("", answer)):
        for raw in match.group(1).split(","):
            index = int(raw)
            if index in seen:
                continue
            if not 1 <= index <= len(chunks):
                logger.warning(
                    "citation [%d] is outside the %d sources", index, len(chunks)
                )
                continue
            seen.add(index)
            chunk = chunks[index - 1]
            citations.append(
                Citation(
                    index=index,
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    ordinal=chunk.ordinal,
                    headings=chunk.headings,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    text=chunk.text,
                )
            )
    return citations
