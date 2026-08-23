"""About screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Center, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Markdown, Rule

from crucible import __version__
from crucible.ui.widgets import ScreenHeader

ABOUT_MD = f"""
# CRUCIBLE v{__version__}

**The LLM testing grounds** — powered by [ATHLAS.IO](https://athlas.io)

A crucible is a vessel in which materials are tested under extreme heat — and a
severe trial that produces something new. This one is for language models.

## What it does

**⚖ Benchmark.** Measure model speed on this machine: time-to-first-token,
prompt processing and generation tokens/s, with a live hardware readout while
it runs. Ollama runs report *exact* server-side timings; OpenAI-compatible
backends (vLLM and friends) report streamed usage counts. Every run ends with
a Markdown report, saved for comparison.

**⚗ Session.** Spawn a disposable Docker container and hand a model the keys:
it gets a `run_shell` tool (or a fenced-block protocol when the backend has no
tool calling) and works through your task one command at a time. You approve
each command in a modal — or flip to auto and watch it go. A Markdown
transcript is written live.

**⌘ Harness.** A plain conversation with a model — until it needs hands. Attach
a terminal or a real headless browser mid-chat and the model gets tools for
whatever is attached, keeping its browsing session, cookies and logins between
calls. Nothing attached is simply a chat. A transcript is written live.

**⌾ Reports.** Every benchmark, session and harness is saved automatically;
browse and read them without leaving the terminal.

**⚙ Config.** One TOML file selects your backend — **Ollama**, **vLLM**, or any
OpenAI-compatible endpoint — plus model defaults, benchmark parameters, sandbox
limits and harness resources. Every setting says what it does and which mode
uses it.

## Keys

- **Global** — `esc` back · `ctrl+q` quit
- **Benchmark** — `s` start / stop
- **Session** — `ctrl+s` stop task · `ctrl+t` auto on/off · `ctrl+d` end session
- **Harness** — `ctrl+r` attach a resource · `ctrl+s` stop · `ctrl+t` auto on/off
  · `ctrl+d` end harness
- **Approval modal** — `y` run · `n` deny · `a` auto from now on

## Links

- Repository: `https://github.com/AthlasSoftware/crucible`
- License: MIT © ATHLAS.IO
"""


class AboutScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE ▸ About", f"v{__version__} · MIT · ATHLAS.IO")
        yield Rule()
        with Center():
            with VerticalScroll(id="about-scroll"):
                yield Markdown(ABOUT_MD)
        yield Footer()
