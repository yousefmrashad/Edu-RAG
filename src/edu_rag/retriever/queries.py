"""Turn one question into a small set of search queries.

The language model returns a structured plan: each query carries the text to
embed, a type, and an optional instruction. The instruction is what the
embedding model is conditioned on, so it has to describe the retrieval task,
not repeat the query. When the model omits one, the query's type supplies the
default.

The model is not trusted to be well behaved: a failed call, an empty plan, or
duplicate text all fall back to something usable rather than failing the
request before retrieval starts.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from edu_rag.clients.llm import LlmError, OpenRouterClient
from edu_rag.config import (
    DEFAULT_QUERY_INSTRUCTION,
    HYDE_QUERY_INSTRUCTION,
    TOPIC_QUERY_INSTRUCTION,
    settings,
)

logger = logging.getLogger(__name__)

_PROMPT = Path(__file__).parent.parent / "prompts" / "query_generation.md"
_SYSTEM_PROMPT = _PROMPT.read_text(encoding="utf-8").format(
    max_queries=settings.retrieve_queries
)


class QueryType(StrEnum):
    """What the query text is, which picks its default instruction."""

    QUESTION = "question"
    TOPIC = "topic"
    HYPOTHETICAL = "hypothetical"


INSTRUCTION_BY_TYPE: dict[QueryType, str] = {
    QueryType.QUESTION: DEFAULT_QUERY_INSTRUCTION,
    QueryType.TOPIC: TOPIC_QUERY_INSTRUCTION,
    QueryType.HYPOTHETICAL: HYDE_QUERY_INSTRUCTION,
}


class GeneratedQuery(BaseModel):
    """One query and the instruction to embed it with."""

    model_config = ConfigDict(extra="forbid")

    query: str
    type: QueryType
    # Empty means "use the type's default". Kept required so the JSON schema
    # stays strict-mode friendly.
    instruction: str


class QueryPlan(BaseModel):
    """The model's top-level object: JSON schema mode needs an object."""

    model_config = ConfigDict(extra="forbid")

    queries: list[GeneratedQuery] = Field(
        max_length=settings.retrieve_queries,
        description="The search queries to run, in priority order.",
    )


async def generate_queries(
    question: str,
    client: OpenRouterClient,
) -> list[GeneratedQuery]:
    """Generate a query plan, retrying once, then falling back to the question."""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    # A strict schema can reject a plan that overshoots the query cap; one
    # resample is cheap, and the fallback is the question itself.
    try:
        plan = await client.complete_structured(messages, QueryPlan)
    except LlmError:
        logger.warning("query generation failed; retrying once")
        try:
            plan = await client.complete_structured(messages, QueryPlan)
        except LlmError:
            logger.exception("query generation failed; using the question")
            return [_fallback(question)]

    queries = _resolve(plan.queries)
    return queries or [_fallback(question)]


def _resolve(queries: list[GeneratedQuery]) -> list[GeneratedQuery]:
    """Trim, deduplicate, cap, and fill in missing instructions."""
    resolved: list[GeneratedQuery] = []
    seen: set[str] = set()
    for query in queries:
        text = " ".join(query.query.split())
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        resolved.append(
            GeneratedQuery(
                query=text,
                type=query.type,
                instruction=query.instruction.strip()
                or INSTRUCTION_BY_TYPE[query.type],
            )
        )
        if len(resolved) == settings.retrieve_queries:
            break
    return resolved


def _fallback(question: str) -> GeneratedQuery:
    return GeneratedQuery(
        query=question,
        type=QueryType.QUESTION,
        instruction=DEFAULT_QUERY_INSTRUCTION,
    )
