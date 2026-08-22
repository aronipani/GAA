"""One async, batched client for an OpenAI-compatible chat endpoint, with retries.
Deliberately not: a single-prompt path, streaming, conversation state, or a framework.

The Spark is bandwidth-bound (~4 tok/s on a 70B single-stream, hundreds under
concurrency), so the unit of work here is a list of prompts, never one prompt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from src.config import LLMConfig, resolve_api_key

log = logging.getLogger(__name__)

CHAT_COMPLETIONS_PATH: Final[str] = "/chat/completions"
RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 409, 429, 500, 502, 503, 504})

# Spread retries of a batch that all failed at once, without an RNG: a seeded run
# must replay identically, and a thundering herd is what killed the batch anyway.
JITTER_FRACTION: Final[float] = 0.1
MAX_BACKOFF_S: Final[float] = 60.0

Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Completion:
    """One prompt's result. `error` is None on success and the text is then usable."""

    index: int  # position in the prompt list; results always come back in input order
    text: str
    tokens_in: int
    tokens_out: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class BatchResult:
    completions: list[Completion]
    tokens_in: int
    tokens_out: int
    wall_clock_s: float
    # Responses that carried no usage block. The report's token counts are only
    # trustworthy when this is zero, so it is surfaced rather than swallowed.
    usage_missing: int = 0
    attempts: int = field(default=0)  # total HTTP requests, retries included

    @property
    def failures(self) -> list[Completion]:
        return [c for c in self.completions if not c.ok]

    @property
    def texts(self) -> list[str]:
        return [c.text for c in self.completions]


class LLMError(RuntimeError):
    """A batch could not be attempted at all (bad config, no key, unreachable host)."""


def _backoff_seconds(attempt: int, base: float, index: int) -> float:
    """Exponential, capped, with a deterministic per-prompt offset."""
    delay = min(base * (2**attempt), MAX_BACKOFF_S)
    return delay * (1.0 + JITTER_FRACTION * ((index % 10) / 10.0))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Honour the server's own pacing when it gives one; it knows its queue depth."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # http-date form; the exponential backoff is a fine fallback


class LLMClient:
    """Async client for one model on one gateway. Use as an async context manager."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self.config = config
        # Injected in tests so `make test` needs no network and no secrets.
        self._api_key = api_key if api_key is not None else resolve_api_key(config)
        self._sleep: Sleeper = sleeper if sleeper is not None else asyncio.sleep
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._attempts = 0
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout_s,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete_many(
        self,
        prompts: list[str],
        *,
        system: str | None = None,
        seed: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> BatchResult:
        """Run every prompt concurrently, bounded by max_concurrency. Input order out.

        A prompt that exhausts its retries comes back as a failed Completion rather
        than an exception: one bad line must not lose a six-hour labelling pass.
        """
        if not prompts:
            return BatchResult(completions=[], tokens_in=0, tokens_out=0, wall_clock_s=0.0)

        self._attempts = 0
        started = time.monotonic()
        completions = await asyncio.gather(
            *(
                self._complete_one(index, prompt, system, seed, response_format)
                for index, prompt in enumerate(prompts)
            )
        )
        elapsed = time.monotonic() - started

        failed = sum(1 for c in completions if not c.ok)
        if failed:
            log.warning("%d/%d prompts failed after retries", failed, len(prompts))

        return BatchResult(
            completions=list(completions),
            tokens_in=sum(c.tokens_in for c in completions),
            tokens_out=sum(c.tokens_out for c in completions),
            wall_clock_s=elapsed,
            usage_missing=sum(1 for c in completions if c.ok and c.tokens_in == 0),
            attempts=self._attempts,
        )

    def _request_body(
        self,
        prompt: str,
        system: str | None,
        seed: int | None,
        response_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if seed is not None:
            body["seed"] = seed
        if response_format is not None:
            body["response_format"] = response_format
        return body

    async def _complete_one(
        self,
        index: int,
        prompt: str,
        system: str | None,
        seed: int | None,
        response_format: dict[str, Any] | None,
    ) -> Completion:
        body = self._request_body(prompt, system, seed, response_format)
        last_error = "no attempt was made"

        async with self._semaphore:
            for attempt in range(self.config.max_retries + 1):
                self._attempts += 1
                try:
                    response = await self._client.post(CHAT_COMPLETIONS_PATH, json=body)
                except httpx.HTTPError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    wait = _backoff_seconds(attempt, self.config.backoff_base_s, index)
                else:
                    if response.status_code == httpx.codes.OK:
                        try:
                            return _parse_completion(index, response.json())
                        except (ValueError, KeyError, TypeError) as exc:
                            # A 200 with an unusable body is the gateway's problem,
                            # not a transient one; retrying it just burns the batch.
                            return Completion(index, "", 0, 0, error=f"bad response: {exc}")
                    if response.status_code not in RETRYABLE_STATUS:
                        return Completion(
                            index, "", 0, 0, error=f"HTTP {response.status_code}"
                        )
                    last_error = f"HTTP {response.status_code}"
                    wait = _retry_after_seconds(response) or _backoff_seconds(
                        attempt, self.config.backoff_base_s, index
                    )

                if attempt < self.config.max_retries:
                    await self._sleep(wait)

        return Completion(
            index, "", 0, 0, error=f"{last_error} after {self.config.max_retries + 1} attempts"
        )


def _parse_completion(index: int, payload: dict[str, Any]) -> Completion:
    choices = payload["choices"]
    if not choices:
        raise ValueError("response contained no choices")
    text = choices[0]["message"]["content"]
    if text is None:
        raise ValueError("response message had null content")
    usage = payload.get("usage") or {}
    return Completion(
        index=index,
        text=text,
        tokens_in=int(usage.get("prompt_tokens", 0)),
        tokens_out=int(usage.get("completion_tokens", 0)),
    )
