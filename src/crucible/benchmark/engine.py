"""Benchmark orchestration: warmup, timed runs, aggregation.

Three modes:
- "prompts" — the quick suite: real prompts, runs × prompts generations.
- "std"     — standardized pp/tg tests (llama-bench conventions: pp512/tg128,
              N reps, mean ± stdev; synthetic deterministic prompts).
- "depth"   — context-depth sweep: pp/tg measured with d tokens resident in the
              KV cache (llama-bench `-d` / sweep-bench convention).

Latency statistics (TTFT/TPOT/ITL/E2EL, NIM formulary) are computed in every
mode from per-chunk timestamps; ITL is pooled across runs before percentiling.
"""

from __future__ import annotations

import statistics
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from itertools import pairwise

from crucible.backends.base import BackendError, GenResult, LLMBackend
from crucible.benchmark.prompts import Prompt
from crucible.benchmark.synthetic import synthetic_text
from crucible.hardware import HardwareInfo

SCHEMA_VERSION = 2

MODES = ("prompts", "std", "depth")

# progress_cb receives dicts like:
#   {"model": str, "phase": "warmup"|"prime"|"run"|"model-done",
#    "done": int, "total": int, "prompt_id": str, "token": str | None}
ProgressCb = Callable[[dict], None]

METRIC_DEFS = {
    "tpot": "(e2el - ttft) / (gen_tokens - 1)",
    "itl": "inter-chunk gap, first token excluded; a chunk may exceed one token "
           "on openai-compatible backends",
    "pp": "prompt tokens / server prompt_eval_duration (ollama, exact) or "
          "prompt tokens / ttft (openai-compat, approx)",
    "tg": "generated tokens / server eval_duration (ollama, exact) or "
          "wall clock first→last chunk (openai-compat)",
    "source": "NVIDIA NIM benchmarking metric formulary; llama-bench pp/tg conventions",
}


@dataclass
class BenchmarkParams:
    runs: int = 3
    max_tokens: int = 256
    warmup: bool = True
    prompt_set: str = "default"
    mode: str = "prompts"  # prompts | std | depth
    pp_tokens: int = 512
    tg_tokens: int = 128
    reps: int = 5
    seed: int = 0
    depths: list[int] = field(default_factory=lambda: [0, 4096, 8192, 16384])


@dataclass
class ModelResult:
    model: str
    load_s: float | None
    runs: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    tests: list[dict] = field(default_factory=list)  # std/depth aggregates
    error: str | None = None


def _stats(values: list[float]) -> dict:
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "stdev": 0.0}
    return {
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def _percentiles(values: list[float]) -> dict:
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    ordered = sorted(values)

    def pct(p: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        pos = p / 100 * (len(ordered) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)

    return {"p50": pct(50), "p95": pct(95), "p99": pct(99)}


def _itl_gaps(r: GenResult) -> list[float]:
    """Inter-token (inter-chunk) latencies, first token excluded."""
    return [b - a for a, b in pairwise(r.chunk_offsets_s)]


def run_row(prompt_id: str, r: GenResult) -> dict:
    tpot_s = ((r.total_s - r.ttft_s) / (r.gen_tokens - 1)
              if r.gen_tokens > 1 else None)
    return {
        "prompt_id": prompt_id,
        "ttft_s": round(r.ttft_s, 4),
        "prompt_tokens": r.prompt_tokens,
        "prompt_tps": round(r.prompt_tps, 2),
        "gen_tokens": r.gen_tokens,
        "gen_tps": round(r.gen_tps, 2),
        "total_s": round(r.total_s, 4),
        "tpot_ms": round(tpot_s * 1000, 2) if tpot_s is not None else None,
        "itl_ms": [round(g * 1000, 2) for g in _itl_gaps(r)],
        "approx": r.approx,
    }


def summarize(rows: list[dict]) -> dict:
    tpots = [r["tpot_ms"] for r in rows if r.get("tpot_ms") is not None]
    # Pool ITL gaps across all runs before percentiling — averaging per-run
    # averages hides stutter (the documented LLMPerf flaw).
    pooled_itl = [g for r in rows for g in r.get("itl_ms", [])]
    ttfts = [r["ttft_s"] for r in rows]
    e2els = [r["total_s"] for r in rows]
    return {
        "ttft_s": {**_stats(ttfts), **_percentiles(ttfts)},
        "gen_tps": _stats([r["gen_tps"] for r in rows]),
        "prompt_tps": _stats([r["prompt_tps"] for r in rows]),
        "tpot_ms": {**_stats(tpots), **_percentiles(tpots)},
        "itl_ms": {**_stats(pooled_itl), **_percentiles(pooled_itl)},
        "e2el_s": {**_stats(e2els), **_percentiles(e2els)},
        "total_gen_tokens": sum(r["gen_tokens"] for r in rows),
        "approx": any(r["approx"] for r in rows),
    }


def aggregate_test(name: str, depth: int, rows: list[dict],
                   which: str, cache_hit: bool = True) -> dict:
    """One llama-bench-style test aggregate (mean ± stdev over reps)."""
    tps = [r["prompt_tps"] if which == "pp" else r["gen_tps"] for r in rows]
    return {
        "test": name,
        "depth": depth,
        "reps": len(rows),
        "tps_mean": round(statistics.fmean(tps), 2) if tps else 0.0,
        "tps_stdev": round(statistics.stdev(tps), 2) if len(tps) > 1 else 0.0,
        "approx": any(r["approx"] for r in rows),
        "cache_hit": cache_hit,
        "samples": rows,
    }


class BenchmarkRunner:
    def __init__(
        self,
        backend: LLMBackend,
        models: list[str],
        prompts: list[Prompt],
        params: BenchmarkParams,
        hardware: HardwareInfo,
        progress_cb: ProgressCb | None = None,
    ):
        self.backend = backend
        self.models = models
        self.prompts = prompts
        self.params = params
        self.hardware = hardware
        self.progress_cb = progress_cb or (lambda _: None)
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    @property
    def total_per_model(self) -> int:
        p = self.params
        if p.mode == "std":
            return p.reps * 2
        if p.mode == "depth":
            return len(p.depths) * (p.reps * 2 + 1)  # prime + pp + tg per depth
        return p.runs * len(self.prompts)

    def _progress(self, model: str, phase: str, done: int, prompt_id: str = "",
                  token: str | None = None) -> None:
        self.progress_cb({"model": model, "phase": phase, "done": done,
                          "total": self.total_per_model, "prompt_id": prompt_id,
                          "token": token})

    async def run(self) -> dict:
        server_version = await self.backend.server_version()
        results: list[ModelResult] = []
        for model in self.models:
            if self.cancelled:
                break
            results.append(await self._run_model(model))
        return self._report(results, server_version)

    async def _run_model(self, model: str) -> ModelResult:
        load_s: float | None = None
        rows: list[dict] = []
        tests: list[dict] = []
        try:
            if self.params.warmup:
                self._progress(model, "warmup", 0)
                warm = await self.backend.generate_stream(model, "Say OK.", 8)
                load_s = warm.load_s

            if self.params.mode == "std":
                tests, rows = await self._run_std(model)
            elif self.params.mode == "depth":
                tests, rows = await self._run_depth(model)
            else:
                rows = await self._run_prompts(model)
        except BackendError as e:
            return ModelResult(model=model, load_s=load_s, runs=rows,
                               summary=summarize(rows), tests=tests, error=str(e))
        finally:
            unload = getattr(self.backend, "unload", None)
            if unload is not None:
                await unload(model)

        self._progress(model, "model-done", len(rows))
        return ModelResult(model=model, load_s=load_s, runs=rows,
                           summary=summarize(rows), tests=tests)

    # ---- prompts mode (the quick suite) -------------------------------------

    async def _run_prompts(self, model: str) -> list[dict]:
        rows: list[dict] = []
        done = 0
        for _ in range(self.params.runs):
            for prompt in self.prompts:
                if self.cancelled:
                    return rows
                r = await self.backend.generate_stream(
                    model, prompt.text, self.params.max_tokens,
                    on_token=lambda tok, m=model, d=done, p=prompt.id:
                        self._progress(m, "run", d, p, tok),
                )
                rows.append(run_row(prompt.id, r))
                done += 1
                self._progress(model, "run", done, prompt.id)
        return rows

    # ---- std mode: pp512/tg128 (llama-bench conventions) ---------------------

    _DETERMINISTIC = {"temperature": 0}
    _TG_PROMPT = ("Write a long, meandering story about a blacksmith's forge. "
                  "Do not stop until told to.")

    async def _run_std(self, model: str) -> tuple[list[dict], list[dict]]:
        p = self.params
        pp_name, tg_name = f"pp{p.pp_tokens}", f"tg{p.tg_tokens}"
        pp_rows: list[dict] = []
        tg_rows: list[dict] = []
        done = 0
        # Untimed pp warmup: the first prefill after a load/idle runs cold and
        # would wreck the stdev (llama-bench warms up per test the same way).
        await self.backend.generate_stream(
            model, synthetic_text(p.pp_tokens, p.seed + 9999), 1,
            options=dict(self._DETERMINISTIC),
        )
        for rep in range(p.reps):
            if self.cancelled:
                break
            # pp: fresh synthetic prompt each rep (defeats prompt caching)
            r = await self.backend.generate_stream(
                model, synthetic_text(p.pp_tokens, p.seed + rep), 1,
                options={"seed": p.seed, **self._DETERMINISTIC},
            )
            pp_rows.append(run_row(pp_name, r))
            done += 1
            self._progress(model, "run", done, pp_name)
            if self.cancelled:
                break
            # tg: minimal prompt, long generation
            r = await self.backend.generate_stream(
                model, self._TG_PROMPT, p.tg_tokens,
                options={"seed": p.seed + rep, **self._DETERMINISTIC},
            )
            tg_rows.append(run_row(tg_name, r))
            done += 1
            self._progress(model, "run", done, tg_name)
        tests = []
        if pp_rows:
            tests.append(aggregate_test(pp_name, 0, pp_rows, "pp"))
        if tg_rows:
            tests.append(aggregate_test(tg_name, 0, tg_rows, "tg"))
        return tests, pp_rows + tg_rows

    # ---- depth mode: pp/tg @ dN sweep ---------------------------------------

    async def _run_depth(self, model: str) -> tuple[list[dict], list[dict]]:
        p = self.params
        tests: list[dict] = []
        all_rows: list[dict] = []
        done = 0
        for depth in p.depths:
            if self.cancelled:
                break
            if depth == 0:
                std_tests, rows = await self._run_std(model)
                for t in std_tests:
                    t["test"] += " @ d0"
                tests.extend(std_tests)
                all_rows.extend(rows)
                done += p.reps * 2 + 1
                continue

            num_ctx = depth + p.pp_tokens + p.tg_tokens + 256
            ctx = synthetic_text(depth, seed=p.seed + depth)
            try:
                # Prime the server's prompt cache with the context.
                self._progress(model, "prime", done, f"d{depth}")
                await self.backend.generate_stream(
                    model, ctx, 1, options={"num_ctx": num_ctx, **self._DETERMINISTIC},
                )
                done += 1

                pp_name = f"pp{p.pp_tokens} @ d{depth}"
                tg_name = f"tg{p.tg_tokens} @ d{depth}"
                pp_rows: list[dict] = []
                tg_rows: list[dict] = []
                cache_hit = True
                for rep in range(p.reps):
                    if self.cancelled:
                        break
                    tail = synthetic_text(p.pp_tokens, seed=p.seed + 1000 + rep)
                    r = await self.backend.generate_stream(
                        model, ctx + "\n" + tail, 1,
                        options={"num_ctx": num_ctx, **self._DETERMINISTIC},
                    )
                    # If the server re-evaluated the whole context the prompt
                    # cache missed — the row then measures full prefill.
                    if r.prompt_tokens > 1.5 * p.pp_tokens:
                        cache_hit = False
                    pp_rows.append(run_row(pp_name, r))
                    done += 1
                    self._progress(model, "run", done, pp_name)
                    if self.cancelled:
                        break
                    r = await self.backend.generate_stream(
                        model, ctx + "\nContinue the story.", p.tg_tokens,
                        options={"num_ctx": num_ctx, "seed": p.seed + rep,
                                 **self._DETERMINISTIC},
                    )
                    tg_rows.append(run_row(tg_name, r))
                    done += 1
                    self._progress(model, "run", done, tg_name)
                if pp_rows:
                    tests.append(aggregate_test(pp_name, depth, pp_rows, "pp",
                                                cache_hit=cache_hit))
                if tg_rows:
                    tests.append(aggregate_test(tg_name, depth, tg_rows, "tg"))
                all_rows.extend(pp_rows + tg_rows)
            except BackendError as e:
                # Partial sweep is still a result: record the skip and continue.
                tests.append({"test": f"pp/tg @ d{depth}", "depth": depth,
                              "skipped": f"{e}"[:200]})
                continue
        return tests, all_rows

    def _report(self, results: list[ModelResult], server_version: str | None) -> dict:
        hw = self.hardware
        return {
            "schema": SCHEMA_VERSION,
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "backend": self.backend.name,
            "base_url": getattr(self.backend, "base_url", ""),
            "server": {"name": self.backend.name, "version": server_version},
            "metric_defs": METRIC_DEFS,
            "hardware": {
                "cpu": hw.cpu_model,
                "cores": hw.cores_logical,
                "ram_gb": round(hw.ram_total_gb, 1),
                "os": hw.os_name,
                "gpus": [{"name": g.name, "vram_mb": g.vram_total_mb} for g in hw.gpus],
            },
            "params": asdict(self.params),
            "results": [
                {
                    "model": r.model,
                    "load_s": round(r.load_s, 3) if r.load_s is not None else None,
                    "runs": r.runs,
                    "summary": r.summary,
                    **({"tests": r.tests} if r.tests else {}),
                    **({"error": r.error} if r.error else {}),
                }
                for r in results
            ],
        }
