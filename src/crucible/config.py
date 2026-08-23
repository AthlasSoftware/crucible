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


@dataclass
class HarnessCfg:
    """Free-form chat with resources attached. Separate from SandboxCfg because
    a harness terminal is a workbench you keep around, not a one-task sandbox —
    different lifetime, and the browser needs network where sessions default off.
    """

    terminal_image: str = "ubuntu:24.04"
    # Playwright ships the browser binaries in the image; first spawn pulls ~2 GB.
    browser_image: str = "mcr.microsoft.com/playwright/python:v1.55.0-noble"
    cpus: float = 4.0
    memory: str = "4g"
    # Terminals follow the sandbox convention (off unless you ask for it); the
    # browser resource always gets network, since a browser without one is a prop.
    terminal_network: bool = False
    approval: str = "approve-each"
    command_timeout_s: int = 60
    # Tool calls the model may chain inside a single reply before we hand the
    # turn back to you — stops a loop from running away between your messages.
    max_tool_calls: int = 12
    # SearXNG instance for the search resource. Empty = autodetect a docker
    # container with "searxng" in its name (its JSON API must be enabled:
    # settings.yml → search.formats: [html, json]).
    searxng_url: str = ""


@dataclass
class UiCfg:
    """How model output is presented."""

    # Models emit Markdown constantly, so rendering it is the readable default.
    # Turn it off when you need to see exactly what came over the wire — this is
    # a testing ground, and the raw bytes are sometimes the whole point.
    render_markdown: bool = True


@dataclass
class VllmServerCfg:
    """A vLLM that CRUCIBLE starts itself, so you can switch models from here.

    vLLM fixes its model at launch, so changing model means replacing the
    server. These are the knobs that replacement needs.
    """

    # Off by default: spawning a server is a bigger thing to do to someone's
    # machine than talking to one, so it should be opted into.
    enabled: bool = False
    image: str = "vllm/vllm-openai:latest"
    # Not 8000 — that is where a vLLM you started yourself usually lives, and
    # colliding with it is exactly what this must never do.
    port: int = 8100
    # Defaults to a volume of our own, so a fresh install is self-contained.
    # Point it at an existing cache (volume name or host path) to share weights
    # with a server you already run.
    hf_cache: str = "crucible-hf-cache"
    hf_token: str = ""
    tensor_parallel_size: int = 1
    gpu_memory_utilization: float = 0.90
    max_model_len: int = 8192
    # Weight loading is minutes, not seconds, and grows with model size.
    startup_timeout_s: int = 900
    extra_args: list[str] = field(default_factory=list)


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
    harness: HarnessCfg = field(default_factory=HarnessCfg)
    vllm_server: VllmServerCfg = field(default_factory=VllmServerCfg)
    ui: UiCfg = field(default_factory=UiCfg)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        return cls(
            backend=_merge_dataclass(BackendCfg, data.get("backend", {})),
            benchmark=_merge_dataclass(BenchmarkCfg, data.get("benchmark", {})),
            sandbox=_merge_dataclass(SandboxCfg, data.get("sandbox", {})),
            harness=_merge_dataclass(HarnessCfg, data.get("harness", {})),
            vllm_server=_merge_dataclass(VllmServerCfg, data.get("vllm_server", {})),
            ui=_merge_dataclass(UiCfg, data.get("ui", {})),
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
