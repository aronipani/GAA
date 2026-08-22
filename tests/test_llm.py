"""Tests for the batched LLM client, against a mock transport - no network, no key.
Deliberately not: testing a real gateway; that is `make serve-status`, not `make test`.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from src.config import LLMConfig
from src.llm import BatchResult, LLMClient, _backoff_seconds

GATEWAY = "http://gateway.test/v1"


def make_config(**overrides: object) -> LLMConfig:
    defaults: dict[str, object] = {
        "host": "spark",
        "base_url": GATEWAY,
        "model": "test-model",
        "api_key_env": "PARSER_LAB_GATEWAY_KEY",
        "max_concurrency": 4,
        "timeout_s": 5.0,
        "max_retries": 2,
        "backoff_base_s": 0.5,
        "temperature": 0.0,
        "max_tokens": 128,
    }
    defaults.update(overrides)
    return LLMConfig(**defaults)  # type: ignore[arg-type]


def chat_response(text: str, tokens_in: int = 7, tokens_out: int = 3) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
        },
    )


class Recorder:
    """Collects requests and the sleeps the client asked for, so nothing really sleeps."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def client_for(
    handler, config: LLMConfig | None = None, recorder: Recorder | None = None
) -> tuple[LLMClient, Recorder]:
    rec = recorder or Recorder()

    def wrapped(request: httpx.Request) -> httpx.Response:
        rec.requests.append(request)
        return handler(request)

    llm = LLMClient(
        config or make_config(),
        api_key="sk-test",
        transport=httpx.MockTransport(wrapped),
        sleeper=rec.sleep,
    )
    return llm, rec


async def test_returns_results_in_input_order() -> None:
    """Concurrency reorders completion; it must not reorder results."""

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][-1]["content"]
        return chat_response(f"parsed:{prompt}")

    llm, _ = client_for(handler)
    async with llm:
        result = await llm.complete_many(["a", "b", "c", "d", "e"])

    assert result.texts == [f"parsed:{p}" for p in "abcde"]
    assert [c.index for c in result.completions] == [0, 1, 2, 3, 4]


async def test_sums_token_counts_for_the_report() -> None:
    llm, _ = client_for(lambda _: chat_response("x", tokens_in=10, tokens_out=4))
    async with llm:
        result = await llm.complete_many(["a", "b", "c"])

    assert (result.tokens_in, result.tokens_out) == (30, 12)
    assert result.usage_missing == 0
    assert result.wall_clock_s >= 0.0


async def test_missing_usage_block_is_surfaced_not_swallowed() -> None:
    """Token counts go straight into the report; a silent zero would be a lie."""
    response = httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})
    llm, _ = client_for(lambda _: response)
    async with llm:
        result = await llm.complete_many(["a", "b"])

    assert result.tokens_in == 0
    assert result.usage_missing == 2


class CountingTransport(httpx.AsyncBaseTransport):
    """Yields to the event loop mid-request, so overlapping requests are observable."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0)
        self.in_flight -= 1
        return chat_response("x")


@pytest.mark.parametrize("limit", [1, 3, 8])
async def test_concurrency_is_bounded_by_the_semaphore(limit: int) -> None:
    """The whole hardware strategy is batching; unbounded fan-out floods the gateway."""
    transport = CountingTransport()
    llm = LLMClient(
        make_config(max_concurrency=limit),
        api_key="sk-test",
        transport=transport,
        sleeper=Recorder().sleep,
    )
    async with llm:
        await llm.complete_many([str(i) for i in range(20)])

    assert transport.peak == limit  # saturated, and never over


async def test_prompts_really_do_run_concurrently() -> None:
    """A client that batches but serialises would pass every other test here."""
    transport = CountingTransport()
    llm = LLMClient(
        make_config(max_concurrency=5), api_key="sk-test", transport=transport
    )
    async with llm:
        await llm.complete_many([str(i) for i in range(10)])

    assert transport.peak > 1


async def test_there_is_no_single_prompt_path() -> None:
    assert not hasattr(LLMClient, "complete")
    assert not hasattr(LLMClient, "complete_one")


async def test_empty_batch_sends_no_requests() -> None:
    llm, rec = client_for(lambda _: chat_response("x"))
    async with llm:
        result = await llm.complete_many([])

    assert result == BatchResult(completions=[], tokens_in=0, tokens_out=0, wall_clock_s=0.0)
    assert rec.requests == []


async def test_request_body_comes_entirely_from_config() -> None:
    """Rule 9: no hardcoded endpoints or model names anywhere in src/."""
    llm, rec = client_for(lambda _: chat_response("x"), make_config(model="qwen-test"))
    async with llm:
        await llm.complete_many(["hello"], system="you are a parser", seed=1337)

    body = rec.body()
    assert body["model"] == "qwen-test"
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 128
    assert body["seed"] == 1337  # recorded in the report; runs must be replayable
    assert body["messages"] == [
        {"role": "system", "content": "you are a parser"},
        {"role": "user", "content": "hello"},
    ]
    assert str(rec.requests[0].url) == f"{GATEWAY}/chat/completions"
    assert rec.requests[0].headers["authorization"] == "Bearer sk-test"


async def test_omits_optional_keys_when_not_asked_for() -> None:
    llm, rec = client_for(lambda _: chat_response("x"))
    async with llm:
        await llm.complete_many(["hello"])

    body = rec.body()
    assert "seed" not in body
    assert "response_format" not in body
    assert [m["role"] for m in body["messages"]] == ["user"]


async def test_retries_a_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return chat_response("ok")

    llm, rec = client_for(handler)
    async with llm:
        result = await llm.complete_many(["a"])

    assert result.completions[0].ok
    assert result.completions[0].text == "ok"
    assert len(rec.sleeps) == 1


async def test_honours_retry_after() -> None:
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"retry-after": "2.5"})
        return chat_response("ok")

    llm, rec = client_for(handler)
    async with llm:
        await llm.complete_many(["a"])

    assert rec.sleeps == [2.5]


async def test_exhausted_retries_fail_that_prompt_only() -> None:
    """A six-hour batch must not be lost to one prompt the gateway keeps rejecting."""

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["messages"][-1]["content"] == "poison":
            return httpx.Response(500)
        return chat_response("ok")

    llm, _ = client_for(handler)
    async with llm:
        result = await llm.complete_many(["good", "poison", "also good"])

    assert [c.ok for c in result.completions] == [True, False, True]
    assert "HTTP 500" in (result.completions[1].error or "")
    assert "3 attempts" in (result.completions[1].error or "")  # 1 try + 2 retries
    assert len(result.failures) == 1


async def test_client_errors_are_not_retried() -> None:
    """A 400 is a bug in our request; retrying it just multiplies the bug."""
    llm, rec = client_for(lambda _: httpx.Response(400, json={"error": "bad model"}))
    async with llm:
        result = await llm.complete_many(["a"])

    assert not result.completions[0].ok
    assert result.completions[0].error == "HTTP 400"
    assert len(rec.requests) == 1
    assert rec.sleeps == []


async def test_transport_errors_are_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("connection refused", request=request)
        return chat_response("ok")

    llm, rec = client_for(handler)
    async with llm:
        result = await llm.complete_many(["a"])

    assert result.completions[0].text == "ok"
    assert len(rec.sleeps) == 2


async def test_a_200_with_an_unusable_body_is_not_retried() -> None:
    llm, rec = client_for(lambda _: httpx.Response(200, json={"choices": []}))
    async with llm:
        result = await llm.complete_many(["a"])

    assert "bad response" in (result.completions[0].error or "")
    assert len(rec.requests) == 1


async def test_retries_can_be_disabled() -> None:
    llm, rec = client_for(lambda _: httpx.Response(500), make_config(max_retries=0))
    async with llm:
        result = await llm.complete_many(["a"])

    assert len(rec.requests) == 1
    assert "1 attempts" in (result.completions[0].error or "")


@pytest.mark.parametrize("attempt,expected", [(0, 1.0), (1, 2.0), (2, 4.0), (10, 60.0)])
def test_backoff_is_exponential_and_capped(attempt: int, expected: float) -> None:
    assert _backoff_seconds(attempt, base=1.0, index=0) == expected


def test_backoff_jitter_is_deterministic() -> None:
    """Seeded runs must replay identically, so the jitter comes from the index, not an RNG."""
    first = _backoff_seconds(1, base=1.0, index=7)
    assert first == _backoff_seconds(1, base=1.0, index=7)
    assert first != _backoff_seconds(1, base=1.0, index=3)
