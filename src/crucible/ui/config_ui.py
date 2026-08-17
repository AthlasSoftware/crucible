"""Config screen: edit the important fields in-TUI, or open the file in $EDITOR."""

from __future__ import annotations

import os
import subprocess

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, Label, Select, Static, Switch

from crucible.config import APPROVAL_MODES, BACKEND_NAMES, config_path, load


class ConfigScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("ctrl+s", "save", "Save"),
        ("ctrl+e", "edit_file", "Open in $EDITOR"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("CRUCIBLE — Config", classes="screen-title")
        with VerticalScroll():
            yield Static(f"[dim]{config_path()}[/]  ·  ctrl+s save · ctrl+e $EDITOR")
            yield Static("[bold]Backend[/] [dim]— used by benchmarks ⚖ and sessions ⚗[/]",
                         classes="screen-title")
            yield from self._field(
                "active backend",
                Select([(n, n) for n in BACKEND_NAMES], id="cfg-active", allow_blank=False),
                "which LLM server benchmarks and sessions talk to")
            yield from self._field("ollama url", Input(id="cfg-ollama-url"),
                                   "where your Ollama server listens (default 127.0.0.1:11434)")
            yield from self._field("ollama model", Input(id="cfg-ollama-model"),
                                   "preselected model in pickers when Ollama is active")
            yield from self._field("vllm url", Input(id="cfg-vllm-url"),
                                   "your vLLM server's OpenAI-compatible endpoint")
            yield from self._field("vllm model", Input(id="cfg-vllm-model"),
                                   "preselected model when vLLM is active (empty = first listed)")
            yield from self._field("openai url", Input(id="cfg-openai-url"),
                                   "any other OpenAI-compatible endpoint (e.g. a cloud API)")
            yield from self._field("openai api key", Input(id="cfg-openai-key", password=True),
                                   "bearer token for that endpoint, if it needs one")
            yield Static("[bold]Benchmark ⚖[/] [dim]— only affects benchmark runs[/]",
                         classes="screen-title")
            yield from self._field("runs", Input(id="cfg-runs", type="integer"),
                                   "times each of the 4 prompts is generated per model — "
                                   "more runs = steadier averages, longer benchmark")
            yield from self._field("max tokens", Input(id="cfg-maxtok", type="integer"),
                                   "cap on generated tokens per run — bigger = longer, "
                                   "steadier gen-tokens/s measurement")
            yield from self._field("warmup", Switch(id="cfg-warmup"),
                                   "one untimed generation first, so cold model loading "
                                   "doesn't skew run 1")
            yield Static("[bold]Sandbox ⚗[/] [dim]— only affects sessions[/]",
                         classes="screen-title")
            yield from self._field("image", Input(id="cfg-image"),
                                   "docker image the model gets its shell in")
            yield from self._field("cpus", Input(id="cfg-cpus", type="number"),
                                   "CPU limit for the sandbox container")
            yield from self._field("memory", Input(id="cfg-memory"),
                                   "memory limit for the sandbox container, e.g. 4g")
            yield from self._field("network", Switch(id="cfg-network"),
                                   "give the sandbox internet access (needed for apt/pip) — "
                                   "off = fully isolated")
            yield from self._field(
                "approval",
                Select([(m, m) for m in APPROVAL_MODES], id="cfg-approval", allow_blank=False),
                "approve-each: you confirm every command with y/n · auto: the model runs freely")
            yield from self._field("command timeout s", Input(id="cfg-timeout", type="integer"),
                                   "seconds before a sandbox command is killed")
            yield from self._field("max iterations", Input(id="cfg-iters", type="integer"),
                                   "command steps per task before the agent loop stops itself")
        yield Footer()

    @staticmethod
    def _field(label: str, widget, desc: str):
        yield Horizontal(Label(label), widget, classes="form-row")
        yield Static(f"[dim]{desc}[/]", classes="form-desc")

    def on_mount(self) -> None:
        self._populate()

    def _populate(self) -> None:
        cfg = self.app.config
        self.query_one("#cfg-active", Select).value = cfg.backend.active
        self.query_one("#cfg-ollama-url", Input).value = cfg.backend.ollama.base_url
        self.query_one("#cfg-ollama-model", Input).value = cfg.backend.ollama.default_model
        self.query_one("#cfg-vllm-url", Input).value = cfg.backend.vllm.base_url
        self.query_one("#cfg-vllm-model", Input).value = cfg.backend.vllm.default_model
        self.query_one("#cfg-openai-url", Input).value = cfg.backend.openai.base_url
        self.query_one("#cfg-openai-key", Input).value = cfg.backend.openai.api_key
        self.query_one("#cfg-runs", Input).value = str(cfg.benchmark.runs)
        self.query_one("#cfg-maxtok", Input).value = str(cfg.benchmark.max_tokens)
        self.query_one("#cfg-warmup", Switch).value = cfg.benchmark.warmup
        self.query_one("#cfg-image", Input).value = cfg.sandbox.image
        self.query_one("#cfg-cpus", Input).value = str(cfg.sandbox.cpus)
        self.query_one("#cfg-memory", Input).value = cfg.sandbox.memory
        self.query_one("#cfg-network", Switch).value = cfg.sandbox.network
        self.query_one("#cfg-approval", Select).value = cfg.sandbox.approval
        self.query_one("#cfg-timeout", Input).value = str(cfg.sandbox.command_timeout_s)
        self.query_one("#cfg-iters", Input).value = str(cfg.sandbox.max_iterations)

    def action_save(self) -> None:
        cfg = self.app.config
        try:
            cfg.backend.active = str(self.query_one("#cfg-active", Select).value)
            cfg.backend.ollama.base_url = self.query_one("#cfg-ollama-url", Input).value.strip()
            cfg.backend.ollama.default_model = self.query_one(
                "#cfg-ollama-model", Input).value.strip()
            cfg.backend.vllm.base_url = self.query_one("#cfg-vllm-url", Input).value.strip()
            cfg.backend.vllm.default_model = self.query_one(
                "#cfg-vllm-model", Input).value.strip()
            cfg.backend.openai.base_url = self.query_one("#cfg-openai-url", Input).value.strip()
            cfg.backend.openai.api_key = self.query_one("#cfg-openai-key", Input).value.strip()
            cfg.benchmark.runs = int(self.query_one("#cfg-runs", Input).value or "3")
            cfg.benchmark.max_tokens = int(self.query_one("#cfg-maxtok", Input).value or "256")
            cfg.benchmark.warmup = self.query_one("#cfg-warmup", Switch).value
            cfg.sandbox.image = self.query_one("#cfg-image", Input).value.strip() or "ubuntu:24.04"
            cfg.sandbox.cpus = float(self.query_one("#cfg-cpus", Input).value or "4")
            cfg.sandbox.memory = self.query_one("#cfg-memory", Input).value.strip() or "4g"
            cfg.sandbox.network = self.query_one("#cfg-network", Switch).value
            cfg.sandbox.approval = str(self.query_one("#cfg-approval", Select).value)
            cfg.sandbox.command_timeout_s = int(
                self.query_one("#cfg-timeout", Input).value or "60")
            cfg.sandbox.max_iterations = int(self.query_one("#cfg-iters", Input).value or "20")
        except ValueError as e:
            self.notify(f"Invalid value: {e}", severity="error")
            return
        path = cfg.save()
        self.notify(f"Saved to {path}")

    def action_edit_file(self) -> None:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        self.app.config.save()  # make sure the file reflects current state
        with self.app.suspend():
            subprocess.run([editor, str(config_path())], check=False)
        self.app.config = load()
        self._populate()
        self.notify("Config reloaded")
