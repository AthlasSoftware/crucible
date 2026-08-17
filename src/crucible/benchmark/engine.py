"""Benchmark orchestration: warmup, timed runs, aggregation."""

from __future__ import annotations

import statistics
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

from crucible.backends.base import GenResult, LLMBackend
from crucible.benchmark.prompts import Prompt
from crucible.hardware import HardwareInfo

SCHEMA_VERSION = 1

# progress_cb receives dicts like:
#   {"model": str, "phase": "warmup"|"run"|"model-done", "done": int, "total": int,
#    "prompt_id": str, "token": str | None}
ProgressCb = Callable[[dict], None]


@dataclass
class BenchmarkParams:
    runs: int = 3
    max_tokens: int = 256
    warmup: bool = True
    prompt_set: str = "default"


@dataclass
class ModelResult:
    model: str
    load_s: float | None
    runs: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
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


def run_row(prompt_id: str, r: GenResult) -> dict:
    return {
        "prompt_id": prompt_id,
        "ttft_s": round(r.ttft_s, 4),
        "prompt_tokens": r.prompt_tokens,
        "prompt_tps": round(r.prompt_tps, 2),
        "gen_tokens": r.gen_tokens,
        "gen_tps": round(r.gen_tps, 2),
        "total_s": round(r.total_s, 4),
        "approx": r.approx,
    }


def summarize(rows: list[dict]) -> dict:
    return {
        "ttft_s": _stats([r["ttft_s"] for r in rows]),
        "gen_tps": _stats([r["gen_tps"] for r in rows]),
        "prompt_tps": _stats([r["prompt_tps"] for r in rows]),
        "total_gen_tokens": sum(r["gen_tokens"] for r in rows),
        "approx": any(r["approx"] for r in rows),
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
        return self.params.runs * len(self.prompts)

    async def run(self) -> dict:
        results: list[ModelResult] = []
        for model in self.models:
            if self.cancelled:
                break
            results.append(await self._run_model(model))
        return self._report(results)

    async def _run_model(self, model: str) -> ModelResult:
        from crucible.backends.base import BackendError

        load_s: float | None = None
        rows: list[dict] = []
        try:
            if self.params.warmup:
                self.progress_cb({"model": model, "phase": "warmup", "done": 0,
                                  "total": self.total_per_model, "prompt_id": "", "token": None})
                warm = await self.backend.generate_stream(model, "Say OK.", 8)
                load_s = warm.load_s

            done = 0
            for _ in range(self.params.runs):
                for prompt in self.prompts:
                    if self.cancelled:
                        break
                    r = await self.backend.generate_stream(
                        model,
                        prompt.text,
                        self.params.max_tokens,
                        on_token=lambda tok, m=model, d=done, p=prompt.id: self.progress_cb(
                            {"model": m, "phase": "run", "done": d,
                             "total": self.total_per_model, "prompt_id": p, "token": tok}
                        ),
                    )
                    rows.append(run_row(prompt.id, r))
                    done += 1
                    self.progress_cb({"model": model, "phase": "run", "done": done,
                                      "total": self.total_per_model, "prompt_id": prompt.id,
                                      "token": None})
        except BackendError as e:
            return ModelResult(model=model, load_s=load_s, runs=rows,
                               summary=summarize(rows), error=str(e))
        finally:
            unload = getattr(self.backend, "unload", None)
            if unload is not None:
                await unload(model)

        self.progress_cb({"model": model, "phase": "model-done", "done": len(rows),
                          "total": self.total_per_model, "prompt_id": "", "token": None})
        return ModelResult(model=model, load_s=load_s, runs=rows, summary=summarize(rows))

    def _report(self, results: list[ModelResult]) -> dict:
        hw = self.hardware
        return {
            "schema": SCHEMA_VERSION,
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "backend": self.backend.name,
            "base_url": getattr(self.backend, "base_url", ""),
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
                    **({"error": r.error} if r.error else {}),
                }
                for r in results
            ],
        }
