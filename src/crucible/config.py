"""Configuration: TOML at ~/.config/crucible/config.toml, autocreated with defaults.

Read with stdlib tomllib, written with tomli-w. Unknown keys are ignored and
missing keys fall back to defaults, so configs survive version skew in both
directions.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import tomli_w

APPROVAL_MODES = ("approve-each", "auto")
BACKEND_NAMES = ("ollama", "vllm", "openai")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    root = Path(base) if base else Path.home() / ".config"
    return root / "crucible"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME", "")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "crucible"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class EndpointCfg:
    base_url: str = ""
    default_model: str = ""
    api_key: str = ""


@dataclass
class BackendCfg:
    active: str = "ollama"
    ollama: EndpointCfg = field(
        default_factory=lambda: EndpointCfg("http://127.0.0.1:11434", "llama3.1:8b")
    )
    vllm: EndpointCfg = field(default_factory=lambda: EndpointCfg("http://127.0.0.1:8000"))
    openai: EndpointCfg = field(default_factory=EndpointCfg)

    def endpoint(self, name: str | None = None) -> EndpointCfg:
        name = name or self.active
        if name not in BACKEND_NAMES:
            raise ValueError(f"unknown backend {name!r}, expected one of {BACKEND_NAMES}")
        return getattr(self, name)


@dataclass
class BenchmarkCfg:
    runs: int = 3
    max_tokens: int = 256
    warmup: bool = True
    prompt_set: str = "default"


@dataclass
class SandboxCfg:
    image: str = "ubuntu:24.04"
    cpus: float = 4.0
    memory: str = "4g"
    network: bool = False
    approval: str = "approve-each"
    command_timeout_s: int = 60
    max_iterations: int = 20


def _merge_dataclass(cls, data: dict):
    """Build a dataclass instance from a dict, keeping defaults for missing keys
    and ignoring unknown ones. Recurses into nested dataclass fields."""
    instance = cls()
    if not isinstance(data, dict):
        return instance
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(instance, f.name)
        if hasattr(current, "__dataclass_fields__"):
            setattr(instance, f.name, _merge_dataclass(type(current), value))
        elif isinstance(value, type(current)) or (
            isinstance(current, float) and isinstance(value, int)
        ):
            setattr(instance, f.name, float(value) if isinstance(current, float) else value)
    return instance


@dataclass
class Config:
    backend: BackendCfg = field(default_factory=BackendCfg)
    benchmark: BenchmarkCfg = field(default_factory=BenchmarkCfg)
    sandbox: SandboxCfg = field(default_factory=SandboxCfg)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        return cls(
            backend=_merge_dataclass(BackendCfg, data.get("backend", {})),
            benchmark=_merge_dataclass(BenchmarkCfg, data.get("benchmark", {})),
            sandbox=_merge_dataclass(SandboxCfg, data.get("sandbox", {})),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path | None = None) -> Path:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomli_w.dumps(self.to_dict()), encoding="utf-8")
        return path


def load(path: Path | None = None) -> Config:
    """Load config, creating the file with defaults on first run."""
    path = path or config_path()
    if not path.exists():
        cfg = Config()
        cfg.save(path)
        return cfg
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return Config()
    return Config.from_dict(data)
