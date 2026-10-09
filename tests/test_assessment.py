from crucible.backends.base import GenResult
from crucible.benchmark.assessment import assess_result
from crucible.benchmark.engine import run_row, summarize
from crucible.report import render_benchmark_md


def sample():
    rows = [run_row("p", GenResult("text", .2, 50, 31, 1, 1.2,
                                  prompt_eval_s=.1, chunk_offsets_s=[.2 + i / 30 for i in range(31)]))]
    result = {"model": "example", "runs": rows, "summary": summarize(rows)}
    return {"schema": 3, "status": "complete", "backend": "ollama",
            "params": {"warmup": True}, "results": [result]}, result


def test_verdict_uses_explicit_targets_and_keeps_sample_limits():
    report, result = sample()
    assessment = assess_result(report, result)
    assert all(m["status"] == "meets target" for m in assessment["metrics"])
    assert any("Only 1 requests" in w for w in assessment["warnings"])
    report["params"].update(ttft_target_s=.1, gen_tps_target=40, chunk_gap_target_ms=10)
    assert all(m["status"] == "misses target" for m in assess_result(report, result)["metrics"])


def test_missing_usage_is_not_a_token_throughput_score():
    report, result = sample()
    result["runs"][0]["token_count_source"] = "chunks"
    assessment = assess_result(report, result)
    assert assessment["metrics"][1]["status"] == "not measured"
    assert any("chunks are not tokens" in w for w in assessment["warnings"])


def test_hidden_reasoning_is_not_graded_as_visible_text_speed():
    report, result = sample()
    result["runs"][0]["reasoning_observed"] = True
    assessment = assess_result(report, result)
    assert assessment["metrics"][1]["status"] == "not measured"
    assert any("hidden thinking" in w for w in assessment["warnings"])


def test_empty_and_cancelled_are_not_successful_zero_latency():
    report, result = sample()
    report["status"] = "cancelled"
    result["runs"] = []
    result["summary"] = summarize([])
    assessment = assess_result(report, result)
    assert assessment["evidence"] == "unavailable"
    md = render_benchmark_md(report)
    assert "metrics unavailable" in md
    assert "**0.0 t/s**" not in md
    assert "cancelled" in md


def test_report_explains_goals_and_does_not_hide_failure_samples():
    report, result = sample()
    result["error"] = "lost connection after first request"
    md = render_benchmark_md(report)
    assert "not an industry grade" in md
    assert "meets target" in md
    assert "ERROR" in md and "| p |" in md
