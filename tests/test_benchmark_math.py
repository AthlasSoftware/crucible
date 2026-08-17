from crucible.backends.base import GenResult
from crucible.benchmark.engine import run_row, summarize


def _result(gen_tokens=100, gen_s=1.0, ttft=0.2, prompt_tokens=50, prompt_eval_s=None,
             approx=False) -> GenResult:
    return GenResult(
        text="x",
        ttft_s=ttft,
        prompt_tokens=prompt_tokens,
        gen_tokens=gen_tokens,
        gen_s=gen_s,
        total_s=ttft + gen_s,
        prompt_eval_s=prompt_eval_s,
        approx=approx,
    )


def test_gen_tps():
    assert _result(gen_tokens=100, gen_s=2.0).gen_tps == 50.0
    assert _result(gen_s=0.0).gen_tps == 0.0


def test_prompt_tps_exact_vs_approx():
    exact = _result(prompt_tokens=100, prompt_eval_s=0.5, ttft=2.0)
    assert exact.prompt_tps == 200.0  # uses server-side eval time
    approx = _result(prompt_tokens=100, prompt_eval_s=None, ttft=2.0)
    assert approx.prompt_tps == 50.0  # falls back to wall-clock TTFT


def test_summarize():
    rows = [
        run_row("p1", _result(gen_tokens=100, gen_s=1.0)),   # 100 tps
        run_row("p2", _result(gen_tokens=300, gen_s=2.0)),   # 150 tps
    ]
    s = summarize(rows)
    assert s["gen_tps"]["mean"] == 125.0
    assert s["gen_tps"]["min"] == 100.0
    assert s["gen_tps"]["max"] == 150.0
    assert s["gen_tps"]["stdev"] > 0
    assert s["total_gen_tokens"] == 400
    assert s["approx"] is False


def test_summarize_flags_approx():
    rows = [run_row("p1", _result()), run_row("p2", _result(approx=True))]
    assert summarize(rows)["approx"] is True


def test_summarize_empty():
    s = summarize([])
    assert s["gen_tps"]["mean"] == 0.0
    assert s["total_gen_tokens"] == 0
