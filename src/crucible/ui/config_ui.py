"""Config screen: sectioned one-line rows (label · widget · description), visible Save."""

from __future__ import annotations

import os
import subprocess

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    Rule,
    Select,
    Static,
    Switch,
)

from crucible.config import APPROVAL_MODES, BACKEND_NAMES, config_path, load
from crucible.ui.widgets import ConfirmModal, ScreenHeader


class ConfigScreen(Screen):
    BINDINGS = [
        ("escape", "back", "Back"),
        ("ctrl+s", "save", "Save"),
        ("ctrl+e", "edit_file", "Open in $EDITOR"),
    ]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE ▸ Config", str(config_path()))
        yield Rule()
        with VerticalScroll(id="config-scroll", classes="hero"):
            yield Static("BACKENDS [dim]· used by ⚖ benchmark and ⚗ session[/]",
                     classes="micro first")
            yield from self._field(
                "active backend",
                    Select([(n, n) for n in BACKEND_NAMES], id="cfg-active",
                       allow_blank=False),
                "which LLM server benchmarks and sessions talk to")
            yield from self._field("ollama url", Input(id="cfg-ollama-url"),
                                   "where your Ollama server listens")
            yield from self._field("ollama model", Input(id="cfg-ollama-model"),
                                   "preselected model in pickers when Ollama is active")
            yield from self._field("vllm url", Input(id="cfg-vllm-url"),
                                   "your vLLM server's OpenAI-compatible endpoint")
            yield from self._field("vllm model", Input(id="cfg-vllm-model"),
                                   "preselected model when vLLM is active")
            yield from self._field("openai url", Input(id="cfg-openai-url"),
                                   "any other OpenAI-compatible endpoint")
            yield from self._field("openai api key",
                                   Input(id="cfg-openai-key", password=True),
                                   "bearer token for that endpoint, if needed")
            yield Static("BENCHMARK ⚖ [dim]· only affects benchmark runs[/]",
                     classes="micro")
            yield from self._field("runs", Input(id="cfg-runs", type="integer"),
                                   "times each of the 4 prompts is generated per "
                                   "model — more = steadier averages")
            yield from self._field("max tokens", Input(id="cfg-maxtok", type="integer"),
                                   "cap on generated tokens per run — bigger = "
                                   "steadier gen-t/s")
            yield from self._field("warmup", Switch(id="cfg-warmup"),
                                   "one untimed generation first, so cold model "
                                   "loading doesn't skew run 1")
            yield Static("SANDBOX ⚗ [dim]· only affects sessions[/]",
                     classes="micro")
            yield from self._field("image", Input(id="cfg-image"),
                                   "docker image the model gets its shell in")
            yield from self._field("cpus", Input(id="cfg-cpus", type="number"),
                                   "CPU limit for the sandbox container")
            yield from self._field("memory", Input(id="cfg-memory"),
                                   "memory limit for the container, e.g. 4g")
            yield from self._field("network", Switch(id="cfg-network"),
                                   "internet for the sandbox (needed for apt/pip) — "
                                   "off = fully isolated")
            yield from self._field(
                "approval",
                    Select([(m, m) for m in APPROVAL_MODES], id="cfg-approval",
                       allow_blank=False),
                "approve-each: confirm every command · auto: the model runs freely")
            yield from self._field("command timeout s",
                                   Input(id="cfg-timeout", type="integer"),
                                   "seconds before a sandbox command is killed")
            yield from self._field("max iterations",
                                   Input(id="cfg-iters", type="integer"),
                                   "command steps per task before the loop stops")
        with Horizontal(id="cfg-actions"):
            yield Button("✓ Save changes", variant="primary", id="cfg-save")
            yield Static("", id="cfg-dirty")
        yield Footer()

    @staticmethod
    def _field(label: str, widget, desc: str):
        yield Horizontal(
            Label(label), widget, Static(desc, classes="form-desc-inline"),
            classes="form-row",
        )

    def on_mount(self) -> None:
        self._loading = True
        self._dirty = False
        self._populate()
        # Changed events from _populate arrive async — re-arm dirty tracking
        # only after they have been processed.
        self.call_after_refresh(self._finish_loading)

    def _finish_loading(self) -> None:
        self._loading = False
        self._update_dirty(False)

    def on_screen_resume(self) -> None:
        if not self._dirty:
            self._loading = True
            self._populate()
            self.call_after_refresh(self._finish_loading)

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
        self._update_dirty(False)

    # ---- dirty tracking -----------------------------------------------------

    def _update_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.query_one("#cfg-dirty", Static).update("unsaved changes ●" if dirty else "")
        marker = " [$warning]●[/]" if dirty else ""
        self.query_one(ScreenHeader).set_context(f"{config_path()}{marker}")

    def _mark_dirty(self) -> None:
        if not getattr(self, "_loading", True):
            self._update_dirty(True)

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._mark_dirty()

    def on_select_changed(self, _event: Select.Changed) -> None:
        self._mark_dirty()

    def on_switch_changed(self, _event: Switch.Changed) -> None:
        self._mark_dirty()

    # ---- actions ------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cfg-save":
            self.action_save()

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
        self._update_dirty(False)
        self.notify(f"Saved to {path}")

    def action_back(self) -> None:
        if not self._dirty:
            self.app.pop_screen()
            return

        def confirmed(answer: bool | None) -> None:
            if answer:
                self._loading = True
                self._populate()
                self._loading = False
                self.app.pop_screen()

        self.app.push_screen(
            ConfirmModal("Discard unsaved changes?", yes_label="Discard", no_label="Stay"),
            confirmed,
        )

    def action_edit_file(self) -> None:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        self.app.config.save()
        with self.app.suspend():
            subprocess.run([editor, str(config_path())], check=False)
        cfg_new = load()
        self.app.config.backend = cfg_new.backend
        self.app.config.benchmark = cfg_new.benchmark
        self.app.config.sandbox = cfg_new.sandbox
        self._loading = True
        self._populate()
        self._loading = False
        self.notify("Config reloaded")
