"""Shared, goal-based result interpretation for the TUI and exported reports.

The defaults are example interactive-chat goals, not hardware rankings or
industry pass marks. Evidence limitations remain visible even when goals pass.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict


def _target(params: dict, key: str, default: float) -> float:
    value = params.get(key, default)
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else default


def assess_result(report: dict, result: dict) -> dict:
    params = report.get("params", {})
    runs = result.get("runs", [])
    summary = result.get("summary", {})
    mode = params.get("mode", "prompts")
    warnings = []
    metrics = []
    n = len(runs)
    if report.get("status") in ("cancelled", "partial", "failed") or result.get("error"):
        warnings.append("Run incomplete or failed; retained samples do not represent a completed suite.")
    if not n:
        warnings.append("No completed measurements; speed and latency are unavailable.")
    if n and n < 20:
        warnings.append(f"Only {n} requests: exploratory evidence. Repeat with more samples before comparing tails.")
    if n and n < 100:
        warnings.append("Request-latency p99 is descriptive for this small sample, not a stable tail estimate.")
    if not params.get("warmup", False):
        warnings.append("Warmup disabled; cold starts can affect the measurements.")
    if report.get("backend") != "ollama" or summary.get("approx"):
        warnings.append("API throughput uses client timing (≈), including transport effects; compare like-for-like backends.")
    if any(r.get("token_count_source") == "chunks" for r in runs):
        warnings.append("Usage counts unavailable: chunks are not tokens. Token throughput is unsuitable for comparison.")
    if any(r.get("reasoning_observed") for r in runs):
        warnings.append("Reasoning output was observed; reported completion tokens may include hidden thinking. Visible-text decode speed is not graded.")
    if any(t.get("skipped") for t in result.get("tests", [])):
        warnings.append("Some context depths were skipped; inspect the per-test notes.")
    if any(t.get("cache_hit", True) is False for t in result.get("tests", [])):
        warnings.append("Context cache reuse was not established; full prefill cannot be compared with cached prefill.")
    if any(t.get("cache_hit", True) is None for t in result.get("tests", [])):
        warnings.append("Server did not expose cached-token counts; context cache reuse is unverified.")
    if mode in ("std", "depth"):
        warnings.append("pp/depth labels are approximate word targets. Compare actual token counts; these are API tests, not llama-bench results.")
    if report.get("schema", 1) < 3:
        warnings.append("Legacy report: timing provenance and completion status may be unavailable.")

    grouped = defaultdict(list)
    for row in runs:
        if row.get("gen_tokens", 0) > 1 and row.get("gen_tps", 0) > 0:
            grouped[row["prompt_id"]].append(row["gen_tps"])
    if any(len(v) > 1 and statistics.stdev(v) / statistics.fmean(v) > .1
           for v in grouped.values()):
        warnings.append("Generation speed varies by more than 10% within a repeated workload; check contention and repeat.")

    def add(label: str, value: float | None, goal: float, unit: str, lower: bool,
            explanation: str) -> None:
        measured = value is not None and math.isfinite(value)
        meets = measured and (value <= goal if lower else value >= goal)
        metrics.append({
            "label": label,
            "value": f"{value:.2f} {unit}" if measured else "unavailable",
            "goal": f"{'≤' if lower else '≥'} {goal:g} {unit}",
            "status": "meets target" if meets else "misses target" if measured else "not measured",
            "explanation": explanation,
        })

    rate_target = _target(params, "gen_tps_target", 20.0)
    if mode == "prompts" and n:
        add("Wait for first text (p95)", summary.get("ttft_s", {}).get("p95"),
            _target(params, "ttft_target_s", 1.0), "s", True,
            "Lower is better; request to first non-empty content chunk, including network and queueing.")
        token_counts_known = not any(r.get("token_count_source") == "chunks" or r.get("reasoning_observed") for r in runs)
        speeds = [r["gen_tps"] for r in runs if r.get("gen_tokens", 0) > 1 and r.get("gen_tps", 0) > 0]
        add("Text generation (mean)", statistics.fmean(speeds) if speeds and token_counts_known else None,
            rate_target, "tok/s", False,
            "Higher is better; single-request decode speed. This does not measure answer quality or serving capacity.")
        gaps = [gap for row in runs for gap in row.get("itl_ms", [])]
        add("Streaming pause (p95)", summary.get("itl_ms", {}).get("p95") if gaps else None,
            _target(params, "chunk_gap_target_ms", 100.0), "ms", True,
            "Lower is better; gaps between received content chunks reveal pauses. Chunks can contain multiple tokens.")
    elif mode in ("std", "depth"):
        for test in result.get("tests", []):
            if test.get("test", "").startswith("tg") and not test.get("skipped"):
                samples = test.get("samples", [])
                known = samples and all(r.get("gen_tokens", 0) > 1 and r.get("gen_tps", 0) > 0
                                        and r.get("token_count_source") != "chunks"
                                        and not r.get("reasoning_observed") for r in samples)
                add(f"Generation {test['test']}", test.get("tps_mean") if known else None,
                    rate_target, "tok/s", False, "Higher is better at the same actual context and output length.")
    return {"samples": n, "metrics": metrics, "warnings": warnings,
            "evidence": "unavailable" if not n else "limited" if warnings else "measured"}


def result_overview(report: dict) -> str:
    """Plain text so model names cannot become Rich markup in the TUI."""
    lines = [f"Run: {report.get('status', 'completion unknown (legacy report)')}",
             "Targets are example chat goals, not an industry grade. Speed does not measure answer quality."]
    for result in report.get("results", []):
        assessment = assess_result(report, result)
        lines.append(f"{result['model']} · {assessment['samples']} completed requests")
        lines.extend(f"  {m['label']}: {m['value']} · {m['status']} ({m['goal']})"
                     for m in assessment["metrics"])
        lines.extend(f"  Note: {w}" for w in assessment["warnings"])
    return "\n".join(lines)
