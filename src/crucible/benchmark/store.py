"""Benchmark result persistence: one JSON file per report under the XDG data dir."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from crucible.config import data_dir


def benchmarks_dir() -> Path:
    return data_dir() / "benchmarks"


@dataclass
class HistoryEntry:
    path: Path
    timestamp: str
    backend: str
    models: list[str]
    mean_gen_tps: dict[str, float]  # model -> mean gen tokens/s


def save(report: dict) -> Path:
    directory = benchmarks_dir()
    directory.mkdir(parents=True, exist_ok=True)
    ts = report["timestamp"].replace(":", "-").replace("+", "_")
    path = directory / f"{ts}_{report['backend']}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def list_history() -> list[HistoryEntry]:
    """Lightweight index of past reports, newest first."""
    directory = benchmarks_dir()
    if not directory.is_dir():
        return []
    entries = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            entries.append(
                HistoryEntry(
                    path=path,
                    timestamp=report.get("timestamp", "?"),
                    backend=report.get("backend", "?"),
                    models=[r["model"] for r in report.get("results", [])],
                    mean_gen_tps={
                        r["model"]: r.get("summary", {}).get("gen_tps", {}).get("mean", 0.0)
                        for r in report.get("results", [])
                    },
                )
            )
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    return entries


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
