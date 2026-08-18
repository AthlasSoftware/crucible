"""Benchmark screen: a walked path — setup ▸ running ▸ results."""

from __future__ import annotations

import time
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Footer,
    Input,
    Label,
    OptionList,
    ProgressBar,
    Rule,
    Select,
    SelectionList,
    Static,
    Switch,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from crucible import hardware
from crucible.backends import BackendError, get_backend
from crucible.benchmark import (
    BenchmarkParams,
    BenchmarkRunner,
    get_prompt_set,
    list_history,
    load_report,
    save,
)
from crucible.config import BACKEND_NAMES
from crucible.ui.widgets import HardwareBar, ScreenHeader, format_bytes

_STREAM_TAIL = 200

RESULT_COLUMNS = (
    "Model", "load s", "gen t/s", "min", "max", "ttft s", "prompt t/s", "tokens", "✓"
)


class BenchmarkScreen(Screen):
    _stage = "bench-setup"  # check_action can run before on_mount
    _backend_error = False

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("s", "start_stop", "start · stop"),
        ("c", "goto_config", "Config"),
    ]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE ▸ Benchmark ▸ setup")
        yield Rule()
        with ContentSwitcher(initial="bench-setup", id="bench-stages"):
            with Vertical(id="bench-setup", classes="hero"):
                with Horizontal(id="bench-setup-cols"):
                    with Vertical(id="bench-steps"):
                        yield Static("BACKEND", classes="micro first")
                        with Horizontal():
                            yield Select(
                                [(n, n) for n in BACKEND_NAMES],
                                id="bench-backend", allow_blank=False,
                            )
                            yield Button("↻ refresh", id="bench-reload",
                                         classes="btn-compact")
                        yield Static("MODELS [dim]· space toggles[/]",
                                     classes="micro")
                        yield SelectionList(id="bench-models")
                        yield Static("", id="bench-models-msg")
                        yield Static("PARAMETERS", classes="micro")
                        with Horizontal():
                            yield Label("runs per prompt")
                            yield Input(id="bench-runs", type="integer")
                            yield Label("max tokens")
                            yield Input(id="bench-maxtok", type="integer")
                        with Horizontal():
                            yield Label("warmup first")
                            yield Switch(id="bench-warmup")
                            yield Static("(untimed load run)", classes="step-hint")
                    with Vertical(id="bench-history-panel"):
                        yield Static("PREVIOUS RUNS", classes="micro first")
                        yield OptionList(id="bench-history")
                        yield Static("enter ▸ view results", classes="step-hint")
                yield Button("Start benchmark", variant="primary", id="bench-start",
                             classes="cta")
            with Vertical(id="bench-run", classes="hero"):
                yield Static("PROGRESS", classes="micro first")
                yield Static("", id="bench-phase")
                yield ProgressBar(id="bench-progress", show_eta=False)
                yield Static("LIVE OUTPUT", classes="micro")
                yield Static("", id="bench-stream")
                yield Button("■ Stop after current generation", id="bench-stop")
            with Vertical(id="bench-results", classes="hero"):
                yield Static("RESULTS", id="results-title", classes="micro first")
                yield DataTable(id="results-table", cursor_type="none",
                                zebra_stripes=True)
                yield Static("", id="results-errors")
                yield Static("", id="results-saved")
                with Horizontal(id="results-actions"):
                    yield Button("▶ Run again", variant="primary", id="results-again")
                    yield Button("◀ New setup", id="results-back")
        yield HardwareBar(id="bench-hw")
        yield Footer()

    def on_mount(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self._stage = "bench-setup"
        self._runner: BenchmarkRunner | None = None
        self._backend_error = False
        self._stream_buf = ""
        self._stream_ts = 0.0
        self._history_entries = []
        self._last_setup: tuple[str, list[str], BenchmarkParams] | None = None

        self.query_one("#results-table", DataTable).add_columns(*RESULT_COLUMNS)
        cfg = self.app.config
        self.query_one("#bench-backend", Select).value = cfg.backend.active
        self.query_one("#bench-runs", Input).value = str(cfg.benchmark.runs)
        self.query_one("#bench-maxtok", Input).value = str(cfg.benchmark.max_tokens)
        self.query_one("#bench-warmup", Switch).value = cfg.benchmark.warmup

        self._refresh_history()
        self._load_models()
        self._update_cta()

    # ---- stages -------------------------------------------------------------

    def _header(self) -> ScreenHeader:
        return self.query_one(ScreenHeader)

    def _set_stage(self, stage: str) -> None:
        self._stage = stage
        self.query_one("#bench-stages", ContentSwitcher).current = stage
        crumbs = {"bench-setup": "setup", "bench-run": "running", "bench-results": "results"}
        self._header().set_crumb(f"CRUCIBLE ▸ Benchmark ▸ {crumbs[stage]}")
        if stage == "bench-setup":
            self._header().set_context(f"backend: {self._backend_name()}")
        self.refresh_bindings()

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "start_stop":
            return self._stage in ("bench-setup", "bench-run")
        if action == "goto_config":
            return self._backend_error
        return True

    def action_start_stop(self) -> None:
        if self._stage == "bench-setup":
            self._start()
        elif self._stage == "bench-run" and self._runner:
            self._stop()

    def action_goto_config(self) -> None:
        self.app.push_screen("config")

    # ---- setup stage --------------------------------------------------------

    def _backend_name(self) -> str:
        value = self.query_one("#bench-backend", Select).value
        return str(value) if value != Select.BLANK else self.app.config.backend.active

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "bench-backend":
            self._header().set_context(f"backend: {self._backend_name()}")
            self._load_models()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "bench-reload": self._load_models,
            "bench-start": self._start,
            "bench-stop": self._stop,
            "results-again": self._run_again,
            "results-back": lambda: self._set_stage("bench-setup"),
        }
        action = actions.get(event.button.id or "")
        if action:
            action()

    def _models_msg(self, markup: str) -> None:
        msg = self.query_one("#bench-models-msg", Static)
        msg.update(markup)
        msg.display = bool(markup)
        self.query_one("#bench-models", SelectionList).display = not markup

    @work(exclusive=True, group="bench-models")
    async def _load_models(self) -> None:
        name = self._backend_name()
        selection_list = self.query_one("#bench-models", SelectionList)
        selection_list.clear_options()
        self._backend_error = False
        self._models_msg(f"contacting {name}…")
        backend = get_backend(self.app.config, name)
        endpoint = self.app.config.backend.endpoint(name)
        try:
            models = await backend.list_models()
        except BackendError:
            self._backend_error = True
            self._models_msg(
                f"[$error]✗[/] can't reach {name} at {endpoint.base_url}\n"
                "Is the server running? Fix the URL in Config (c) · ↻ refresh to retry"
            )
            self.refresh_bindings()
            self._update_cta()
            return
        finally:
            await backend.close()
        if not models:
            self._models_msg(
                f"{name} is up but has no models — try: ollama pull llama3.1:8b"
            )
            self._update_cta()
            return
        self._models_msg("")
        default = endpoint.default_model
        for m in models:
            label = m.id if not m.size_bytes else f"{m.id}  ({format_bytes(m.size_bytes)})"
            selection_list.add_option(
                Selection(label, m.id, m.id == default or len(models) == 1)
            )
        self.refresh_bindings()
        self._update_cta()
        if self._stage == "bench-setup":
            selection_list.focus()

    def on_selection_list_selected_changed(self, _event) -> None:
        self._update_cta()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("bench-runs", "bench-maxtok"):
            self._update_cta()

    def _params(self) -> BenchmarkParams:
        try:
            runs = max(1, int(self.query_one("#bench-runs", Input).value or "3"))
        except ValueError:
            runs = 3
        try:
            max_tokens = max(16, int(self.query_one("#bench-maxtok", Input).value or "256"))
        except ValueError:
            max_tokens = 256
        return BenchmarkParams(
            runs=runs,
            max_tokens=max_tokens,
            warmup=self.query_one("#bench-warmup", Switch).value,
            prompt_set=self.app.config.benchmark.prompt_set,
        )

    def _update_cta(self) -> None:
        button = self.query_one("#bench-start", Button)
        selected = self.query_one("#bench-models", SelectionList).selected
        if not selected:
            button.disabled = True
            button.label = "Select at least one model (step 2)"
            return
        params = self._params()
        prompts = get_prompt_set(params.prompt_set)
        button.disabled = False
        button.label = (
            f"▶  Start benchmark — {len(selected)} model(s) · "
            f"{params.runs} runs × {len(prompts)} prompts"
        )

    def _start(self) -> None:
        models = list(self.query_one("#bench-models", SelectionList).selected)
        if not models:
            self.notify("Select at least one model first (step 2)", severity="warning")
            return
        self._last_setup = (self._backend_name(), models, self._params())
        self._run_benchmark(*self._last_setup)

    def _run_again(self) -> None:
        if self._last_setup:
            self._run_benchmark(*self._last_setup)
        else:
            self._set_stage("bench-setup")

    def _refresh_history(self) -> None:
        history = self.query_one("#bench-history", OptionList)
        history.clear_options()
        self._history_entries = list_history()
        if not self._history_entries:
            history.add_option(
                Option("[dim]No runs yet — your first benchmark\nwill appear here.[/]",
                       disabled=True)
            )
            return
        for i, e in enumerate(self._history_entries):
            when = e.timestamp[5:16].replace("T", " ")
            history.add_option(
                Option(f"{when}  {e.backend}  {len(e.models)} model(s)", id=str(i))
            )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "bench-history":
            return
        try:
            entry = self._history_entries[int(event.option.id or "")]
        except (ValueError, IndexError):
            return
        report = load_report(entry.path)
        self._last_setup = (
            report.get("backend", self._backend_name()),
            [r["model"] for r in report.get("results", [])],
            BenchmarkParams(**report.get("params", {})),
        )
        self._show_results(report, saved_path=entry.path.with_suffix(".md"))

    # ---- run stage ----------------------------------------------------------

    def _stop(self) -> None:
        if self._runner:
            self._runner.cancel()
            stop = self.query_one("#bench-stop", Button)
            stop.label = "stopping…"
            stop.disabled = True

    def _stream(self, token: str) -> None:
        self._stream_buf = (self._stream_buf + token)[-_STREAM_TAIL:]
        now = time.monotonic()
        if now - self._stream_ts > 0.1:
            self._stream_ts = now
            self.query_one("#bench-stream", Static).update(
                self._stream_buf.replace("\n", " ")
            )

    @work(exclusive=True, group="bench-run")
    async def _run_benchmark(self, name: str, models: list[str],
                             params: BenchmarkParams) -> None:
        prompts = get_prompt_set(params.prompt_set)
        per_model = params.runs * len(prompts)
        total = len(models) * per_model
        self._set_stage("bench-run")
        stop = self.query_one("#bench-stop", Button)
        stop.label = "■ Stop after current generation"
        stop.disabled = False
        progress = self.query_one("#bench-progress", ProgressBar)
        progress.update(total=total, progress=0)
        phase = self.query_one("#bench-phase", Static)
        self._done_models = 0
        self._stream_buf = ""
        self.query_one("#bench-stream", Static).update("")

        def model_index(model: str) -> int:
            return models.index(model) + 1 if model in models else 1

        def on_progress(ev: dict) -> None:
            if ev["phase"] == "warmup":
                phase.update(f"[{ev['model']}] warmup (loading model)…")
            elif ev["phase"] == "model-done":
                self._done_models += 1
            elif ev["token"] is not None:
                self._stream(ev["token"])
            else:
                overall = self._done_models * per_model + ev["done"]
                progress.update(progress=overall)
                phase.update(
                    f"{ev['model']} — generation {ev['done']}/{ev['total']} "
                    f"(prompt: {ev['prompt_id']})"
                )
                self._header().set_context(
                    f"model {model_index(ev['model'])}/{len(models)} · "
                    f"gen {ev['done']}/{ev['total']}"
                )

        backend = get_backend(self.app.config, name)
        try:
            self._runner = BenchmarkRunner(
                backend, models, prompts, params, hardware.probe(), on_progress
            )
            report = await self._runner.run()
        except BackendError as e:
            self._show_failure(str(e))
            return
        finally:
            await backend.close()
            self._runner = None

        path = save(report)
        self._refresh_history()
        self._show_results(report, saved_path=path)

    # ---- results stage ------------------------------------------------------

    def _show_failure(self, message: str) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear()
        self.query_one("#results-errors", Static).update("")
        self.query_one("#results-saved", Static).update(
            f"[$error]✗[/] benchmark failed: {message} — check the backend and try again"
        )
        self._set_stage("bench-results")
        self._header().set_context("")
        self.query_one("#results-again", Button).focus()

    def _show_results(self, report: dict, saved_path: Path | None = None) -> None:
        results = report.get("results", [])
        timestamp = report.get("timestamp", "")[:16].replace("T", " ")
        self.query_one("#results-title", Static).update(
            f"RESULTS [dim]— {len(results)} model(s) · {report.get('backend', '?')} · "
            f"{timestamp}[/]"
        )
        table = self.query_one("#results-table", DataTable)
        table.clear()
        errors = []
        for r in results:
            s = r.get("summary", {})
            if r.get("error"):
                table.add_row(r["model"], "—", "—", "—", "—", "—", "—", "—", "error")
                errors.append(f"{r['model']}: {r['error']}")
                continue
            table.add_row(
                r["model"],
                f"{r['load_s']:.1f}" if r.get("load_s") else "—",
                f"{s.get('gen_tps', {}).get('mean', 0):.1f}",
                f"{s.get('gen_tps', {}).get('min', 0):.1f}",
                f"{s.get('gen_tps', {}).get('max', 0):.1f}",
                f"{s.get('ttft_s', {}).get('mean', 0):.2f}",
                f"{s.get('prompt_tps', {}).get('mean', 0):.0f}",
                str(s.get("total_gen_tokens", 0)),
                "≈" if s.get("approx") else "✓",
            )
        self.query_one("#results-errors", Static).update("[dim]" + " · ".join(errors) + "[/]"
                                                         if errors else "")
        saved = self.query_one("#results-saved", Static)
        if saved_path is not None:
            saved.update(
                f"[$success]✓[/] report saved — JSON + Markdown in {saved_path.parent}\n"
                "  read it any time in Reports (esc, then r)"
            )
            self._header().set_context(f"saved: {saved_path.name}")
        else:
            saved.update("")
        self._set_stage("bench-results")
        self.query_one("#results-again", Button).focus()
