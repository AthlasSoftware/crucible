"""Regression coverage for evidence lost or misrepresented by partial runs."""

from unittest.mock import AsyncMock

import pytest

from crucible import hardware
from crucible.backends.base import BackendError, GenResult, ToolCall
from crucible.benchmark.engine import BenchmarkParams, BenchmarkRunner
from crucible.benchmark.prompts import Prompt
from crucible.benchmark.toolcalls import ToolCallRunner


@pytest.fixture
def hw(monkeypatch):
    info = hardware.HardwareInfo("Test CPU", 4, 8, 3000, 0, 32, 4, 100, 80, "Test OS")
    monkeypatch.setattr(hardware, "probe", lambda: info)
    monkeypatch.setattr(BenchmarkRunner, "_snapshot", AsyncMock(return_value={}))
    return info


class Backend:
    name = "ollama"
    base_url = "http://localhost:11434"

    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    async def server_version(self):
        return "test"

    async def model_info(self, model):
        return {}

    async def generate_stream(self, model, prompt, max_tokens, **kwargs):
        self.calls.append((prompt, max_tokens))
        if len(self.calls) == self.fail_at:
            raise BackendError("connection lost")
        return GenResult("same", .2, len(prompt.split()) + 2, max_tokens, 1, 1.2,
                         prompt_eval_s=.1, chunk_offsets_s=[.2, 1.2])


async def test_failure_keeps_completed_samples(hw):
    runner = BenchmarkRunner(Backend(fail_at=2), ["model"], [Prompt("p", "hi")],
                             BenchmarkParams(runs=3, warmup=False), hw)
    report = await runner.run()
    assert report["status"] == "failed"
    assert len(report["results"][0]["runs"]) == 1
    assert report["results"][0]["summary"]["completed_runs"] == 1


async def test_cancel_marks_partial_evidence(hw):
    runner = BenchmarkRunner(Backend(), ["first", "untested"], [Prompt("p", "hi")],
                             BenchmarkParams(runs=3, warmup=False), hw)
    runner.progress_cb = lambda event: runner.cancel() if event["phase"] == "run" else None
    report = await runner.run()
    assert report["status"] == "cancelled"
    assert report["requested_models"] == ["first", "untested"]
    assert len(report["results"]) == 1
    assert len(report["results"][0]["runs"]) == 1


async def test_warmup_setting_and_actual_lengths(hw):
    backend = Backend()
    runner = BenchmarkRunner(backend, ["model"], [],
                             BenchmarkParams(mode="std", warmup=False, reps=2, tg_tokens=64), hw)
    report = await runner.run()
    assert len(backend.calls) == 4
    pp, tg = report["results"][0]["tests"]
    assert pp["prompt_tokens_range"] == [514, 514]
    assert tg["gen_tokens_range"] == [64, 64]
    assert tg["latency"]["ttft_s"]["n"] == 2
    backend.calls.clear()
    runner.params.warmup = True
    await runner.run()
    assert len(backend.calls) == 7  # load, prefill and decode warmups + four measured calls


async def test_depth_progress_and_unknown_cache(hw):
    events = []
    runner = BenchmarkRunner(Backend(), ["model"], [],
                             BenchmarkParams(mode="depth", depths=[0, 32], reps=2,
                                             warmup=False), hw, events.append)
    report = await runner.run()
    progress = [e["done"] for e in events if e["phase"] == "run"]
    assert progress == sorted(progress)
    assert progress[-1] == runner.total_per_model == 9
    assert report["results"][0]["tests"][2]["cache_hit"] is None


async def test_repeatability_does_not_claim_seed_support(hw):
    runner = BenchmarkRunner(Backend(), ["model"], [],
                             BenchmarkParams(mode="determinism", reps=2, warmup=False), hw)
    report = await runner.run()
    verdict = report["results"][0]["tests"][0]["verdict"]
    assert "seed support unproven" in verdict
    assert "seed honored" not in verdict


@pytest.mark.parametrize("fail", [False, True])
async def test_tool_trials_count_only_completed_requests(hw, fail):
    backend = Backend()
    runner = ToolCallRunner(backend, ["model"], trials=3, hardware=hw)
    calls = 0

    async def trial(model, case):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BackendError("connection lost")
        return [ToolCall("1", "get_weather", {"city": "Paris"})]

    runner._one_trial = trial
    if not fail:
        runner.progress_cb = lambda event: runner.cancel()
    report = await runner.run()
    result = report["results"][0]
    assert result["total"] == 1
    assert result["passes"] == 1
    assert result["cases"][0]["trials"] == 1
    assert result["expected_trials"] == 36
    assert result["status"] == ("failed" if fail else "incomplete")
