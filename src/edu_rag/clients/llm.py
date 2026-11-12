"""OpenRouter chat completions, streamed on the API's event loop.

Generation is inline in the request that asked for it, so this only needs the
async path. The SDK owns transport concerns -- pooling, timeouts, typed
errors -- and retries establishing the request, which is the only part that
can be replayed: once a token has reached the browser, a failure surfaces to
the caller instead of silently restarting the answer.

SDK types stop at this module's edge; callers get :class:`Chunk` and
:class:`LlmError`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, cast

from openrouter import OpenRouter, components
from openrouter.errors import OpenRouterError
from openrouter.utils.retries import BackoffStrategy, RetryConfig

logger = logging.getLogger(__name__)

# {"role": "system" | "user" | "assistant", "content": "..."}
Message = dict[str, str]

# 4xx other than 429 means the request or the account is wrong; retrying only
# delays the error the caller needs to see.
_HINTS = {
    401: " (check the API key)",
    402: " (out of credits)",
    403: " (key lacks access to this model)",
    404: " (unknown model id)",
    429: " (rate limited)",
}


class LlmError(RuntimeError):
    """OpenRouter rejected the request, or the stream failed partway through."""


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class Chunk:
    """One increment of a completion.

    ``text`` is usually set; ``finish_reason`` and ``usage`` only on the last
    events, so callers that just want the answer can ignore both.
    """

    text: str = ""
    finish_reason: str | None = None
    usage: Usage | None = None


class OpenRouterClient:
    """Streaming chat client bound to one model and one API key."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        referer: str | None = None,
        title: str | None = None,
        server_url: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("an OpenRouter API key is required")
        if not model:
            raise ValueError("a generation model id is required")

        self._model = model
        self._sdk = OpenRouter(
            api_key=api_key,
            # Optional attribution, shown on the OpenRouter activity page.
            http_referer=referer,
            x_open_router_title=title,
            server_url=server_url,
            timeout_ms=int(timeout * 1000),
            retry_config=RetryConfig(
                strategy="backoff",
                backoff=BackoffStrategy(
                    initial_interval=1_000,
                    max_interval=10_000,
                    exponent=2.0,
                    max_elapsed_time=30_000,
                ),
                retry_connection_errors=True,
            ),
        )

    async def aclose(self) -> None:
        """Release the SDK's HTTP connections."""
        await self._sdk.__aexit__(None, None, None)

    async def stream_chat(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[Chunk]:
        """Yield :class:`Chunk` objects as the completion arrives."""
        chosen_model = model or self._model
        logger.info(
            "chat stream start model=%s messages=%d", chosen_model, len(messages)
        )
        try:
            stream = await self._sdk.chat.send_async(
                # The SDK types messages as its own pydantic union; plain dicts
                # are validated at runtime, so the cast only satisfies the
                # overloads and keeps SDK types out of our callers.
                messages=cast(Any, list(messages)),
                model=chosen_model,
                stream=True,
                # Without this a streamed response reports no token counts.
                stream_options=components.ChatStreamOptions(include_usage=True),
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except OpenRouterError as exc:
            logger.warning("chat stream rejected: %s", _describe(exc))
            raise LlmError(_describe(exc)) from exc

        finish_reason: str | None = None
        usage: Usage | None = None
        completed = False

        # Closing the stream releases the response. An abandoned generator --
        # browser disconnected -- now cancels promptly instead of draining the
        # completion we would still be billed for.
        try:
            async with stream:
                try:
                    async for event in stream:
                        for chunk in _to_chunks(event):
                            finish_reason = chunk.finish_reason or finish_reason
                            usage = chunk.usage or usage
                            yield chunk
                except OpenRouterError as exc:
                    logger.warning("chat stream failed mid-stream: %s", _describe(exc))
                    raise LlmError(_describe(exc)) from exc
            completed = True
        finally:
            # Reached on early close too, so a disconnect is still accounted for.
            logger.info(
                "chat stream end model=%s completed=%s finish_reason=%s usage=%s",
                chosen_model,
                completed,
                finish_reason,
                usage,
            )


def _to_chunks(event: components.ChatStreamChunk) -> list[Chunk]:
    if event.error is not None:
        raise LlmError(
            f"OpenRouter stream error {event.error.code}: {event.error.message}"
        )

    chunks: list[Chunk] = []
    for choice in event.choices:
        delta = choice.delta
        # ``content`` is OptionalNullable: the SDK fills it with an UNSET
        # sentinel when absent, so ``is None`` would treat a usage-only chunk
        # as having text. Only ``content`` too -- a reasoning model's
        # ``delta.reasoning`` is not part of the answer.
        text = delta.content if isinstance(delta.content, str) else ""
        if text or choice.finish_reason:
            chunks.append(Chunk(text=text, finish_reason=choice.finish_reason))

    if event.usage is not None:
        chunks.append(
            Chunk(
                usage=Usage(
                    prompt_tokens=event.usage.prompt_tokens,
                    completion_tokens=event.usage.completion_tokens,
                    total_tokens=event.usage.total_tokens,
                )
            )
        )
    return chunks


def _describe(exc: OpenRouterError) -> str:
    return (
        f"OpenRouter {exc.status_code}{_HINTS.get(exc.status_code, '')}: {exc.message}"
    )
