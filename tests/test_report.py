import json
from pathlib import Path

import pytest

from crucible.report import (
    SessionTranscript,
    list_reports,
    render_benchmark_md,
    save_benchmark_md,
)

REPORT = {
    "schema": 1,
    "timestamp": "2026-08-17T18:00:00+02:00",
    "backend": "ollama",
    "base_url": "http://127.0.0.1:11434",
    "hardware": {"cpu": "TestCPU", "cores": 32, "ram_gb": 128.0, "os": "TestOS",
                 "gpus": [{"name": "RTX 3090", "vram_mb": 24576}]},
    "params": {"runs": 2, "max_tokens": 128, "warmup": True, "prompt_set": "default"},
    "results": [
        {
            "model": "test:8b",
            "load_s": 3.2,
            "runs": [
                {"prompt_id": "qa-short", "ttft_s": 0.1, "prompt_tokens": 10,
                 "prompt_tps": 100.0, "gen_tokens": 50, "gen_tps": 120.5,
                 "total_s": 0.5, "approx": False},
            ],
            "summary": {
                "ttft_s": {"mean": 0.1, "min": 0.1, "max": 0.1, "stdev": 0.0},
                "gen_tps": {"mean": 120.5, "min": 120.5, "max": 120.5, "stdev": 0.0},
                "prompt_tps": {"mean": 100.0, "min": 100.0, "max": 100.0, "stdev": 0.0},
                "total_gen_tokens": 50,
                "approx": False,
            },
        },
        {"model": "broken:1b", "load_s": None, "runs": [], "summary": {},
         "error": "connection refused"},
    ],
}


def test_render_benchmark_md():
    md = render_benchmark_md(REPORT)
    assert "# CRUCIBLE benchmark report" in md
    assert "test:8b" in md and "120.5" in md
    assert "RTX 3090" in md
    assert "qa-short" in md
    assert "**ERROR:** connection refused" in md
    assert "ATHLAS.IO" in md


def test_save_benchmark_md(tmp_path: Path):
    json_path = tmp_path / "run.json"
    json_path.write_text(json.dumps(REPORT))
    md_path = save_benchmark_md(REPORT, json_path)
    assert md_path == tmp_path / "run.md"
    assert "test:8b" in md_path.read_text()


def test_same_second_reports_do_not_overwrite(data_home):
    from crucible.benchmark.store import save

    first = save({**REPORT, "id": "first-id"})
    second = save({**REPORT, "id": "second-id"})
    assert first != second
    assert first.exists() and second.exists()


@pytest.fixture
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path


def test_session_transcript(data_home):
    t = SessionTranscript.create("abc123", "test:8b", "ollama", "ubuntu:24.04", False)
    t.user("make a file")
    t.command("touch /tmp/x")
    t.output("stdout", "ok")
    t.output("stderr", "warning!")
    t.assistant("done making the file")
    t.done("file created")
    t.close()

    content = t.path.read_text()
    assert "session transcript — abc123" in content
    assert "**you ▸** make a file" in content
    assert "$ touch /tmp/x" in content
    assert "! warning!" in content  # stderr marker
    assert "**✓ DONE:** file created" in content
    # fenced block opened by command is closed before the next entry
    assert content.count("```") % 2 == 0


def test_list_reports(data_home):
    t = SessionTranscript.create("s1", "m", "ollama", "img", False)
    t.close()
    bench_dir = data_home / "crucible" / "benchmarks"
    bench_dir.mkdir(parents=True)
    (bench_dir / "2026-01-01_ollama.md").write_text("# report")

    entries = list_reports()
    kinds = {e.kind for e in entries}
    assert kinds == {"benchmark", "session"}
    assert len(entries) == 2
    # newest first
    assert entries[0].mtime >= entries[1].mtime
