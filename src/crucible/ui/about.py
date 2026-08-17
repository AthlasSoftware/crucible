"""About screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Markdown, Static

from crucible import __version__

ABOUT_MD = f"""
# CRUCIBLE v{__version__}

**The LLM testing grounds** — powered by [ATHLAS.IO](https://athlas.io)

A crucible is a vessel in which materials are tested under extreme heat — and a
severe trial that produces something new. This one is for language models.

## What it does

**⚖ Benchmark.** Inspect the machine you're on (CPU, RAM, disk, and live per-GPU
VRAM/utilization) and measure model speed: time-to-first-token, prompt
processing and generation tokens/s. Ollama runs report *exact* server-side
timings; OpenAI-compatible backends (vLLM and friends) report streamed usage
counts. Every run is saved as JSON history for comparison.

**⚗ Sessions.** Spawn a disposable Docker container and hand an LLM the keys:
the model gets a `run_shell` tool (or a fenced-block protocol when the backend
doesn't support tool calling) and works through your task command by command.
You approve each command — or flip to auto and watch it go. Chat on the left,
terminal on the right.

**⚙ Config.** One TOML file (`~/.config/crucible/config.toml`) selects your
backend — **Ollama**, **vLLM**, or any OpenAI-compatible endpoint — plus model
defaults, benchmark parameters, and sandbox limits (image, CPU, memory,
network, approval mode).

## Design notes

- Pure client: CRUCIBLE binds no ports and stores nothing but local JSON.
- Sandboxes are labeled `crucible.session=…`, resource-limited, offline by
  default, and purged on exit.
- Four runtime dependencies. MIT licensed. Built with
  [Textual](https://textual.textualize.io).

## Links

- Repository: `https://github.com/AthlasSoftware/crucible`
- License: MIT © ATHLAS.IO
"""


class AboutScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Static("CRUCIBLE — About", classes="screen-title")
        with VerticalScroll():
            yield Markdown(ABOUT_MD)
        yield Footer()
