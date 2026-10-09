import json
from types import SimpleNamespace

import httpx
import pytest

from crucible.backends import openai_compat
from crucible.backends.base import BackendError
from crucible.backends.ollama import OllamaBackend
from crucible.backends.openai_compat import OpenAICompatBackend


def client_for(body):
    return httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=body)))


def sse(events):
    return "\n\n".join("data: " + (json.dumps(e) if isinstance(e, dict) else e) for e in events)


async def test_api_usage_is_exact_but_timing_is_client_side(monkeypatch):
    backend = OpenAICompatBackend("http://test")
    await backend.close()
    backend._client = client_for(sse([
        {"choices": [{"delta": {"content": "hello"}}]},
        {"choices": [{"delta": {"content": " world"}}]},
        {"usage": {"prompt_tokens": 5, "completion_tokens": 11}},
        "[DONE]",
    ]))
    # The done/usage response is delayed well beyond the final content chunk.
    ticks = iter([0, .2, .5, 2])
    monkeypatch.setattr(openai_compat, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    try:
        result = await backend.generate_stream("model", "hi", 20)
    finally:
        await backend.close()
    assert result.approx is True
    assert result.token_count_source == "server"
    assert result.timing_source == "client"
    assert result.total_s == .5
    assert result.gen_tps == pytest.approx(10 / .3)


@pytest.mark.parametrize("events", [
    [{"choices": [{"delta": {"content": "truncated"}}]}],
    [{"choices": [{"delta": {}}]}, "[DONE]"],
])
async def test_incomplete_or_empty_api_stream_fails_explicitly(events):
    backend = OpenAICompatBackend("http://test")
    await backend.close()
    backend._client = client_for(sse(events))
    try:
        with pytest.raises(BackendError):
            await backend.generate_stream("model", "hi", 20)
    finally:
        await backend.close()


async def test_ollama_prefill_subtracts_reported_cached_tokens():
    backend = OllamaBackend("http://test")
    await backend.close()
    body = [{"response": "hello", "done": False},
            {"done": True, "eval_count": 10, "eval_duration": 1_000_000_000,
             "prompt_eval_count": 100, "prompt_eval_cached_count": 80,
             "prompt_eval_duration": 1_000_000_000}]
    backend._client = client_for("\n".join(json.dumps(row) for row in body))
    try:
        result = await backend.generate_stream("model", "hi", 20)
    finally:
        await backend.close()
    assert result.prompt_tokens == 100
    assert result.cached_prompt_tokens == 80
    assert result.prompt_tps == 20  # duration measures only the uncached tokens
    assert result.approx is False


async def test_api_reasoning_usage_does_not_create_visible_decode_latency():
    from crucible.benchmark.engine import run_row, summarize

    backend = OpenAICompatBackend("http://test")
    await backend.close()
    backend._client = client_for(sse([
        {"choices": [{"delta": {"content": "answer"}}]},
        {"usage": {"prompt_tokens": 5, "completion_tokens": 11,
                   "completion_tokens_details": {"reasoning_tokens": 9}}},
        "[DONE]",
    ]))
    try:
        result = await backend.generate_stream("model", "hi", 20)
    finally:
        await backend.close()
    row = run_row("p", result)
    assert row["reasoning_observed"] is True
    assert row["tpot_ms"] is None
    assert summarize([row])["gen_tps"]["n"] == 0
