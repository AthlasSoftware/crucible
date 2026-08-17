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
            yield Static("[bold]Backend[/]", classes="screen-title")
            yield self._row("active backend", Select(
                [(n, n) for n in BACKEND_NAMES], id="cfg-active", allow_blank=False))
            yield self._row("ollama url", Input(id="cfg-ollama-url"))
            yield self._row("ollama model", Input(id="cfg-ollama-model"))
            yield self._row("vllm url", Input(id="cfg-vllm-url"))
            yield self._row("vllm model", Input(id="cfg-vllm-model"))
            yield self._row("openai url", Input(id="cfg-openai-url"))
            yield self._row("openai api key", Input(id="cfg-openai-key", password=True))
            yield Static("[bold]Benchmark[/]", classes="screen-title")
            yield self._row("runs", Input(id="cfg-runs", type="integer"))
            yield self._row("max tokens", Input(id="cfg-maxtok", type="integer"))
            yield self._row("warmup", Switch(id="cfg-warmup"))
            yield Static("[bold]Sandbox[/]", classes="screen-title")
            yield self._row("image", Input(id="cfg-image"))
            yield self._row("cpus", Input(id="cfg-cpus", type="number"))
            yield self._row("memory", Input(id="cfg-memory"))
            yield self._row("network", Switch(id="cfg-network"))
            yield self._row("approval", Select(
                [(m, m) for m in APPROVAL_MODES], id="cfg-approval", allow_blank=False))
            yield self._row("command timeout s", Input(id="cfg-timeout", type="integer"))
            yield self._row("max iterations", Input(id="cfg-iters", type="integer"))
        yield Footer()

    @staticmethod
    def _row(label: str, widget) -> Horizontal:
        return Horizontal(Label(label), widget, classes="form-row")

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
