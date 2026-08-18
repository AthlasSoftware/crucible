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


def test_percentiles():
    from crucible.benchmark.engine import _percentiles
    p = _percentiles([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    assert p["p50"] == 5.5
    assert 9.5 < p["p95"] <= 10.0
    assert _percentiles([]) == {"p50": 0.0, "p95": 0.0, "p99": 0.0}


def test_synthetic_deterministic():
    from crucible.benchmark.synthetic import synthetic_text
    a = synthetic_text(512, seed=1)
    assert a == synthetic_text(512, seed=1)
    assert a != synthetic_text(512, seed=2)
    assert len(a.split()) == 512


def test_run_row_latency_fields():
    r = _result(gen_tokens=101, gen_s=1.0, ttft=0.5)
    r.total_s = 1.5
    r.chunk_offsets_s = [0.5 + 0.01 * i for i in range(100)]
    row = run_row("tg128", r)
    assert row["tpot_ms"] == 10.0  # (1.5-0.5)/100 = 10ms
    assert len(row["itl_ms"]) == 99


def test_summarize_pools_itl():
    r1 = _result(); r1.chunk_offsets_s = [0.1, 0.2, 0.3]
    r2 = _result(); r2.chunk_offsets_s = [0.1, 0.6, 1.1]  # slow run
    s = summarize([run_row("a", r1), run_row("b", r2)])
    # pooled: gaps 100,100,500,500 ms — p99 must see the stutter
    assert s["itl_ms"]["p99"] > 400


def test_aggregate_test():
    from crucible.benchmark.engine import aggregate_test
    rows = [run_row("pp512", _result(prompt_tokens=512, prompt_eval_s=1.0)),
            run_row("pp512", _result(prompt_tokens=512, prompt_eval_s=2.0))]
    t = aggregate_test("pp512", 0, rows, "pp")
    assert t["reps"] == 2
    assert t["tps_mean"] == 384.0  # (512 + 256) / 2
    assert t["tps_stdev"] > 0
