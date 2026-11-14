"""HTTP clients for the TEI containers.

Embedding and reranking each exist twice: an async pair for the API's request
path, and a sync pair for the Celery ingest worker, whose task is already
blocking. The two transports differ only in their retry loop -- payloads,
batching, backoff and response validation are shared, so the worker cannot
drift into different behaviour from the API under load.

Both are thin: connection pool, retries, payload shape. Batching, truncation
warnings and response validation live in module-level helpers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import httpx

from edu_rag.config import settings

logger = logging.getLogger(__name__)

# Counts equal-length to the inputs it is given.
CountTokens = Callable[[Sequence[str]], Sequence[int]]

CHARS_PER_TOKEN = 4

# 503 while the model loads, 429 while the server queue is full. Both are
# transient; anything else is a real error (bad payload, unknown model).
TRANSIENT_STATUS = frozenset({429, 503})

# Shared backoff: start at a second, double, stop growing at ten.
RETRY_START = 1.0
RETRY_CAP = 10.0

# (index into the input list, relevance score), best first.
Ranking = list[tuple[int, float]]


class TeiError(RuntimeError):
    """A TEI request failed, or failed permanently after retrying."""


def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def _prepare(
    texts: Sequence[str],
    batch_tokens: int,
    max_input_tokens: int,
    count_tokens: CountTokens | None = None,
) -> Iterator[list[str]]:
    """Split inputs into requests that fit the server's token budget."""
    sizes = (
        list(count_tokens(texts))
        if count_tokens is not None
        else [_estimate_tokens(text) for text in texts]
    )
    if len(sizes) != len(texts):
        raise ValueError(f"counted {len(sizes)} inputs, got {len(texts)} to batch")

    batch: list[str] = []
    used = 0
    for text, size in zip(texts, sizes, strict=True):
        if size > max_input_tokens:
            # --auto-truncate turns this into a silent quality loss, so say so:
            # the tail of the chunk becomes unretrievable with no error.
            logger.warning(
                "input of ~%d tokens exceeds the %d-token window; tail dropped",
                size,
                max_input_tokens,
            )
        if batch and used + size > batch_tokens:
            yield batch
            batch, used = [], 0
        batch.append(text)
        used += size
    if batch:
        yield batch


def _embed_payload(inputs: Sequence[str]) -> dict[str, object]:
    return {"inputs": list(inputs), "truncate": True}


def _query_input(text: str, instruction: str) -> str:
    # The template ends in "Query: " -- the trailing space belongs to the
    # model's prompt and the query is concatenated onto it.
    return f"Instruct: {instruction}\nQuery: {text}"


def _rerank_payload(query: str, texts: Sequence[str]) -> dict[str, object]:
    return {"query": query, "texts": list(texts)}


def _vectors(data: Any, dim: int) -> list[list[float]]:
    # A single-input request may come back flat instead of nested.
    if isinstance(data, list) and data and isinstance(data[0], (int, float)):
        data = [data]
    vectors: list[list[float]] = []
    for vector in data:
        values = [float(value) for value in vector]
        if len(values) != dim:
            raise TeiError(
                f"expected {dim}-dim vector, got {len(values)}: "
                "is EMBEDDING_DIM correct?"
            )
        vectors.append(values)
    return vectors


def _ranking(data: Any, top_k: int | None) -> Ranking:
    # TEI already sorts by descending score; sorting again is cheap and guards
    # against version differences in response ordering.
    ranked = sorted(
        ((int(item["index"]), float(item["score"])) for item in data),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return ranked if top_k is None else ranked[:top_k]


def _next_delay(delay: float) -> float:
    return min(delay * 2.0, RETRY_CAP)


def _check_attempts(attempts: int) -> None:
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")


def _response_error(path: str, response: httpx.Response) -> TeiError:
    return TeiError(f"POST {path} -> {response.status_code}: {response.text[:200]}")


async def _post(
    http: httpx.AsyncClient, path: str, payload: dict[str, object], attempts: int
) -> httpx.Response:
    attempt = 1
    delay = RETRY_START
    while True:
        try:
            response = await http.post(path, json=payload)
        except httpx.TransportError as exc:
            if attempt == attempts:
                raise TeiError(f"POST {path} failed: {exc}") from exc
            logger.warning("POST %s failed (%s), retrying", path, exc)
        else:
            if response.is_success:
                return response
            if response.status_code not in TRANSIENT_STATUS or attempt == attempts:
                raise _response_error(path, response)
            logger.warning(
                "POST %s -> %s, retrying in %.1fs", path, response.status_code, delay
            )
        await asyncio.sleep(delay)
        delay = _next_delay(delay)
        attempt += 1


def _post_sync(
    http: httpx.Client, path: str, payload: dict[str, object], attempts: int
) -> httpx.Response:
    attempt = 1
    delay = RETRY_START
    while True:
        try:
            response = http.post(path, json=payload)
        except httpx.TransportError as exc:
            if attempt == attempts:
                raise TeiError(f"POST {path} failed: {exc}") from exc
            logger.warning("POST %s failed (%s), retrying", path, exc)
        else:
            if response.is_success:
                return response
            if response.status_code not in TRANSIENT_STATUS or attempt == attempts:
                raise _response_error(path, response)
            logger.warning(
                "POST %s -> %s, retrying in %.1fs", path, response.status_code, delay
            )
        time.sleep(delay)
        delay = _next_delay(delay)
        attempt += 1


class TeiEmbedder:
    """Embedding client. Documents are encoded bare; queries are instructed.

    The instruction is per call, because each rewrite (plain question, topic,
    HyDE passage) is a different kind of input. ``instruction`` here is only
    the fallback for callers that do not pick one.
    """

    def __init__(
        self,
        base_url: str,
        *,
        embedding_dim: int,
        instruction: str | None = None,
        timeout: float = 30.0,
        attempts: int = 5,
        batch_tokens: int = 16_384,
        max_input_tokens: int = 32_768,
        count_tokens: CountTokens | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        self._dim = embedding_dim
        self._instruction = instruction or settings.query_instruction
        _check_attempts(attempts)
        self._attempts = attempts
        self._batch_tokens = batch_tokens
        self._max_input_tokens = max_input_tokens
        self._count_tokens = count_tokens

    async def aclose(self) -> None:
        await self._http.aclose()

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batches = _prepare(
            texts, self._batch_tokens, self._max_input_tokens, self._count_tokens
        )
        for batch in batches:
            response = await _post(
                self._http, "/embed", _embed_payload(batch), self._attempts
            )
            vectors.extend(_vectors(response.json(), self._dim))
        return vectors

    async def embed_queries(
        self, queries: Sequence[tuple[str, str]]
    ) -> list[list[float]]:
        """Embed several instructed queries in one request.

        ``queries`` is ``(text, instruction)`` pairs. The instructions are
        formatted into the model's prompt, so the whole batch is one payload
        instead of one request per query.
        """
        texts = [_query_input(text, instruction) for text, instruction in queries]
        vectors: list[list[float]] = []
        for batch in _prepare(
            texts, self._batch_tokens, self._max_input_tokens, self._count_tokens
        ):
            response = await _post(
                self._http, "/embed", _embed_payload(batch), self._attempts
            )
            vectors.extend(_vectors(response.json(), self._dim))
        return vectors

    async def embed_query(
        self, text: str, *, instruction: str | None = None
    ) -> list[float]:
        """Embed one query; ``instruction`` overrides the client's fallback."""
        return (await self.embed_queries([(text, instruction or self._instruction)]))[0]


class TeiReranker:
    def __init__(
        self, base_url: str, *, timeout: float = 30.0, attempts: int = 5
    ) -> None:
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)
        _check_attempts(attempts)
        self._attempts = attempts

    async def aclose(self) -> None:
        await self._http.aclose()

    async def rerank(
        self, query: str, texts: Sequence[str], *, top_k: int | None = None
    ) -> Ranking:
        """Score ``texts`` against ``query``; return (index, score), best first."""
        if not texts:
            return []
        response = await _post(
            self._http, "/rerank", _rerank_payload(query, texts), self._attempts
        )
        return _ranking(response.json(), top_k)


class SyncTeiEmbedder:
    """Blocking embedder for the Celery worker, which only embeds documents.

    Construct it inside the task, after the process has forked: the pool
    belongs to the process that made it.
    """

    def __init__(
        self,
        base_url: str,
        *,
        embedding_dim: int,
        timeout: float = 30.0,
        attempts: int = 5,
        batch_tokens: int = 16_384,
        max_input_tokens: int = 32_768,
        count_tokens: CountTokens | None = None,
    ) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._dim = embedding_dim
        _check_attempts(attempts)
        self._attempts = attempts
        self._batch_tokens = batch_tokens
        self._max_input_tokens = max_input_tokens
        self._count_tokens = count_tokens

    def close(self) -> None:
        self._http.close()

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batches = _prepare(
            texts, self._batch_tokens, self._max_input_tokens, self._count_tokens
        )
        for batch in batches:
            response = _post_sync(
                self._http, "/embed", _embed_payload(batch), self._attempts
            )
            vectors.extend(_vectors(response.json(), self._dim))
        return vectors


class SyncTeiReranker:
    """Blocking reranker for the Celery worker. Construct after forking."""

    def __init__(
        self, base_url: str, *, timeout: float = 30.0, attempts: int = 5
    ) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        _check_attempts(attempts)
        self._attempts = attempts

    def close(self) -> None:
        self._http.close()

    def rerank(
        self, query: str, texts: Sequence[str], *, top_k: int | None = None
    ) -> Ranking:
        """Score ``texts`` against ``query``; return (index, score), best first."""
        if not texts:
            return []
        response = _post_sync(
            self._http, "/rerank", _rerank_payload(query, texts), self._attempts
        )
        return _ranking(response.json(), top_k)
