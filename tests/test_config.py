from pathlib import Path

from crucible.config import Config, load


def test_defaults():
    cfg = Config()
    assert cfg.backend.active == "ollama"
    assert cfg.backend.ollama.base_url == "http://127.0.0.1:11434"
    assert cfg.backend.vllm.base_url == "http://127.0.0.1:8000"
    assert cfg.sandbox.image == "ubuntu:24.04"
    assert cfg.sandbox.approval == "approve-each"
    assert cfg.benchmark.runs == 3


def test_roundtrip(tmp_path: Path):
    path = tmp_path / "config.toml"
    cfg = Config()
    cfg.backend.active = "vllm"
    cfg.benchmark.runs = 7
    cfg.sandbox.network = True
    cfg.sandbox.cpus = 2.5
    cfg.save(path)

    loaded = load(path)
    assert loaded.backend.active == "vllm"
    assert loaded.benchmark.runs == 7
    assert loaded.sandbox.network is True
    assert loaded.sandbox.cpus == 2.5


def test_autocreate(tmp_path: Path):
    path = tmp_path / "sub" / "config.toml"
    cfg = load(path)
    assert path.exists()
    assert cfg.backend.active == "ollama"


def test_partial_and_unknown_keys(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[backend]\nactive = "openai"\nbogus = 1\n\n[benchmark]\nruns = 9\n'
        '\n[mystery]\nx = true\n'
    )
    cfg = load(path)
    assert cfg.backend.active == "openai"
    assert cfg.benchmark.runs == 9
    # untouched sections keep defaults
    assert cfg.sandbox.image == "ubuntu:24.04"
    assert cfg.benchmark.max_tokens == 256


def test_corrupt_file_falls_back(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text("this is [not toml")
    cfg = load(path)
    assert cfg.backend.active == "ollama"
