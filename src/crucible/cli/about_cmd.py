"""About: what CRUCIBLE is, rendered inline."""

from __future__ import annotations

from rich.markdown import Markdown

from crucible import __version__
from crucible.cli.ui import console, rule
from crucible.config import Config

ABOUT_MD = f"""
**CRUCIBLE v{__version__}** — the LLM testing grounds, powered by [ATHLAS.IO](https://athlas.io)

A crucible is a vessel in which materials are tested under extreme heat — and a
severe trial that produces something new. This one is for language models.

## Capabilities

- **⚖ Benchmark** — inspect the machine you're on (CPU, RAM, disk, live per-GPU
  VRAM/utilization) and measure model speed: time-to-first-token, prompt and
  generation tokens/s. Ollama runs report *exact* server-side timings;
  OpenAI-compatible backends (vLLM and friends) report streamed usage counts.
  Every run ends with a Markdown report you can read right here or in any editor.
- **⚗ Sessions** — spawn a disposable Docker container and hand an LLM the
  keys: a `run_shell` tool (with automatic fenced-protocol fallback for models
  without tool calling), one command at a time, approve each or go auto. Every
  session streams to a transcript file.
- **⌾ Reports** — every benchmark and session leaves a Markdown report on disk;
  browse and read them from the menu.
- **⚙ Config** — one TOML file selects your backend (**Ollama**, **vLLM**, or
  any OpenAI-compatible endpoint), model defaults, benchmark parameters, and
  sandbox limits.

## Design notes

- Pure client: binds no ports, stores nothing but local JSON/Markdown.
- Sandboxes are labeled, resource-limited, offline by default, destroyed on exit.
- Runs inline in your terminal — everything stays in your scrollback.

Repository: https://github.com/AthlasSoftware/crucible · MIT © ATHLAS.IO
"""


async def run(cfg: Config) -> None:
    rule("about")
    console.print(Markdown(ABOUT_MD))
