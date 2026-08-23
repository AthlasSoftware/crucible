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
from crucible.banner import BANNER, BANNER_WIDTH
from crucible.benchmark import (
    BenchmarkParams,
    BenchmarkRunner,
    get_prompt_set,
    list_history,
    load_report,
    save,
)
from crucible.config import BACKEND_NAMES
from crucible.ui.vllm_control import VllmControl
from crucible.ui.widgets import MachineMonitor, ScreenHeader, format_bytes

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
                        with Horizontal(classes="field-row"):
                            yield Select(
                                [(n, n) for n in BACKEND_NAMES],
                                id="bench-backend", allow_blank=False,
                            )
                            yield Button("refresh", id="bench-reload",
                                         classes="btn-compact")
                        yield Static("", id="vllm-status", classes="server-status")
                        yield VllmControl(id="bench-vllm")
                        yield Static("MODELS [dim]· space toggles[/]",
                                     classes="micro")
                        yield SelectionList(id="bench-models")
                        yield Static("", id="bench-models-msg")
                        yield Static("", id="bench-model-facts", classes="step-hint")
                        yield Static("MODE", classes="micro")
                        with Horizontal():
                            yield Select(
                                [
                                    ("quick — 4 real prompts × N runs", "prompts"),
                                    ("standard — pp512 / tg128 · 5 reps", "std"),
                                    ("context sweep — pp/tg @ 0/4k/8k/16k", "depth"),
                                    ("determinism — same prompt × 5, pinned seed", "determinism"),
                                    ("tool calling — 12-case reliability suite", "tools"),
                                ],
                                id="bench-mode", allow_blank=False, value="prompts",
                            )
                        yield Static("", id="bench-mode-hint", classes="step-hint")
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
                yield Static(
                    "\n".join(ln.ljust(BANNER_WIDTH) for ln in BANNER.splitlines()),
                    id="results-banner",
                )
                yield Static("RESULTS", id="results-title", classes="micro")
                yield DataTable(id="results-table", cursor_type="none",
                                zebra_stripes=True)
                yield DataTable(id="tests-table", cursor_type="none",
                                zebra_stripes=True)
                yield Static("", id="results-errors")
                yield Static("", id="results-saved")
                with Horizontal(id="results-actions"):
                    yield Button("▶ Run again", variant="primary", id="results-again")
                    yield Button("◀ New setup", id="results-back")
        yield MachineMonitor(id="bench-hw", compact=True)
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
        self._model_meta: dict = {}
        self._facts_cache: dict[str, dict] = {}

        self.query_one("#results-table", DataTable).add_columns(*RESULT_COLUMNS)
        self.query_one("#tests-table", DataTable).add_columns(
            "Model", "test", "t/s (mean ± σ)", "notes")
        self.query_one("#tests-table", DataTable).display = False
        self._refresh_mode_hint()
        cfg = self.app.config
        self.query_one("#bench-backend", Select).value = cfg.backend.active
        self.query_one("#bench-runs", Input).value = str(cfg.benchmark.runs)
        self.query_one("#bench-maxtok", Input).value = str(cfg.benchmark.max_tokens)
        self.query_one("#bench-warmup", Switch).value = cfg.benchmark.warmup

        self._refresh_history()
        self._sync_vllm_visibility()
        self._load_models()
        self._update_cta()

    def _sync_vllm_visibility(self) -> None:
        """The spawn bar only makes sense for vLLM — Ollama manages its own models."""
        show = self._backend_name() == "vllm" and self.app.config.vllm_server.enabled
        self.query_one("#bench-vllm", VllmControl).display = show
        self.query_one("#vllm-status", Static).display = show

    def on_vllm_control_changed(self, event: VllmControl.Changed) -> None:
        """A server was spawned or stopped — the model list just changed."""
        event.stop()
        self._load_models()

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
            self._sync_vllm_visibility()
            self._load_models()
        elif event.select.id == "bench-mode":
            self._refresh_mode_hint()
            self._update_cta()

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
            control = self.query_one("#bench-vllm", VllmControl)
            if control.display and not control.running:
                # The spawn bar right below already says there is no server and
                # what to do about it; two messages saying it is just noise.
                self._models_msg("")
            else:
                self._models_msg(
                    f"[$error]✗[/] can't reach {name} at {endpoint.base_url}\n"
                    "Is the server running? Fix the URL in Config (c) · refresh to retry"
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
        self._model_meta = {m.id: m for m in models}
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

    def on_selection_list_selection_highlighted(self, event) -> None:
        selection = getattr(event, "selection", None)
        if selection is not None and selection.value:
            self._show_model_facts(str(selection.value))

    @work(exclusive=True, group="bench-facts")
    async def _show_model_facts(self, model: str) -> None:
        """MODEL FACTS for the highlighted model: trained context, configured
        num_ctx, KV cost, and the practical context ceiling on this rig's
        currently-free VRAM — so you know what a depth sweep can reach."""
        import asyncio as _asyncio

        from crucible.benchmark.inspect import context_budget

        facts = self.query_one("#bench-model-facts", Static)
        info = self._facts_cache.get(model)
        if info is None:
            backend = get_backend(self.app.config, self._backend_name())
            try:
                info = await backend.model_info(model)
            except BackendError:
                info = {}
            finally:
                await backend.close()
            self._facts_cache[model] = info
        if not info:
            facts.update("[dim]no metadata exposed by this backend[/]")
            return
        gpus = await _asyncio.to_thread(hardware.probe_gpus)
        free_vram = sum((g.vram_total_mb - g.vram_used_mb) for g in gpus) * 1024 * 1024
        meta = self._model_meta.get(model)
        weights = meta.size_bytes if meta else None
        budget = context_budget(info, weights, free_vram or None)

        bits = []
        trained = info.get("model_context_length")
        if trained:
            bits.append(f"trained ctx [bold]{trained:,}[/]")
        if "num_ctx" in (info.get("parameters") or ""):
            line = next(ln.strip() for ln in info["parameters"].splitlines()
                        if "num_ctx" in ln)
            bits.append(f"configured {line.split()[-1]}")
        else:
            bits.append("[#d7af5f]num_ctx not set → Ollama default[/]")
        if budget:
            bits.append(f"kv ≈ {budget['kv_mb_per_1k']:g} MB/1k tok")
            fit = budget.get("practical_max_ctx")
            if fit is not None:
                if not budget.get("weights_fit", True):
                    bits.append("[#d7af5f]weights alone exceed free VRAM → CPU spill[/]")
                elif budget.get("vram_limited"):
                    bits.append(f"[bold]fits ≈ {fit:,} ctx[/] on "
                                f"{free_vram / 1e9:.1f}G free VRAM")
                else:
                    bits.append(f"[green]full ctx fits[/] on "
                                f"{free_vram / 1e9:.1f}G free VRAM")
        facts.update(f"[bold]{model}[/] — " + " · ".join(bits)
                     + "  [dim](≈ estimate: f16 KV, weights ×1.1, 90% of free)[/]")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("bench-runs", "bench-maxtok"):
            self._update_cta()

    _MODE_HINTS = {
        "prompts": "a quick feel for the model — real prompts, exact server metrics",
        "std": "community-standard prefill (pp512) + decode (tg128) throughput, "
               "mean ± stdev over 5 reps — table is postable next to llama-bench",
        "depth": "pp/tg with 0/4k/8k/16k tokens resident in context — "
                 "does it stay fast when the KV cache fills? (slow on big depths)",
        "determinism": "same prompt 5×, seed pinned, temperature 0 — does the "
                       "backend actually honor the seed?",
        "tools": "12 scored cases × 3 trials: right tool, argument filling, "
                 "parallel calls, abstention — nothing is executed",
    }

    def _mode(self) -> str:
        value = self.query_one("#bench-mode", Select).value
        return str(value) if value != Select.BLANK else "prompts"

    def _refresh_mode_hint(self) -> None:
        self.query_one("#bench-mode-hint", Static).update(
            self._MODE_HINTS.get(self._mode(), ""))

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
            mode=self._mode(),
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
        if params.mode == "std":
            button.label = (f"▶  Standard benchmark — {len(selected)} model(s) · "
                            f"pp{params.pp_tokens} + tg{params.tg_tokens} × {params.reps} reps")
        elif params.mode == "depth":
            depths = "/".join(f"{d // 1024}k" if d else "0" for d in params.depths)
            button.label = (f"▶  Context sweep — {len(selected)} model(s) · "
                            f"depths {depths}")
        elif params.mode == "determinism":
            button.label = (f"▶  Determinism audit — {len(selected)} model(s) · "
                            f"{params.reps} identical runs")
        elif params.mode == "tools":
            button.label = (f"▶  Tool-calling suite — {len(selected)} model(s) · "
                            "12 cases × 3 trials")
        else:
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
        self._set_stage("bench-run")
        stop = self.query_one("#bench-stop", Button)
        stop.label = "■ Stop after current generation"
        stop.disabled = False
        progress = self.query_one("#bench-progress", ProgressBar)
        phase = self.query_one("#bench-phase", Static)
        self._done_models = 0
        self._stream_buf = ""
        self.query_one("#bench-stream", Static).update("")

        def model_index(model: str) -> int:
            return models.index(model) + 1 if model in models else 1

        def on_progress(ev: dict) -> None:
            if ev["phase"] == "warmup":
                phase.update(f"[{ev['model']}] warmup (loading model)…")
            elif ev["phase"] == "prime":
                phase.update(f"[{ev['model']}] priming context {ev['prompt_id']}…")
            elif ev["phase"] == "model-done":
                self._done_models += 1
            elif ev["token"] is not None:
                self._stream(ev["token"])
            else:
                overall = self._done_models * ev["total"] + ev["done"]
                progress.update(progress=overall)
                phase.update(
                    f"{ev['model']} — {ev['done']}/{ev['total']} "
                    f"({ev['prompt_id']})"
                )
                self._header().set_context(
                    f"model {model_index(ev['model'])}/{len(models)} · "
                    f"{ev['done']}/{ev['total']}"
                )

        backend = get_backend(self.app.config, name)
        try:
            if params.mode == "tools":
                from crucible.benchmark.toolcalls import ToolCallRunner

                self._runner = ToolCallRunner(
                    backend, models, trials=3, hardware=hardware.probe(),
                    progress_cb=on_progress,
                )
            else:
                self._runner = BenchmarkRunner(
                    backend, models, prompts, params, hardware.probe(), on_progress
                )
            progress.update(total=len(models) * self._runner.total_per_model,
                            progress=0)
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
        if report.get("kind") == "toolcalls":
            self._show_tool_results(report, saved_path)
            return
        results = report.get("results", [])
        timestamp = report.get("timestamp", "")[:16].replace("T", " ")
        self.query_one("#results-title", Static).update(
            f"RESULTS [dim]— {len(results)} model(s) · {report.get('backend', '?')} · "
            f"{timestamp}[/]"
        )
        tests_table = self.query_one("#tests-table", DataTable)
        tests_table.clear()
        test_rows = [(r["model"], t) for r in results for t in r.get("tests", [])]
        tests_table.display = bool(test_rows)
        for model, t in test_rows:
            if t.get("skipped"):
                tests_table.add_row(model, t["test"], "—", f"skipped: {t['skipped'][:40]}")
                continue
            if t.get("test") == "determinism":
                mark = "✓" if t.get("deterministic") else "✗"
                tests_table.add_row(model, "determinism", "—",
                                    f"{mark} {t.get('verdict', '?')[:60]}")
                continue
            notes = []
            if t.get("approx"):
                notes.append("≈ wall clock")
            if not t.get("cache_hit", True):
                notes.append("full prefill")
            tests_table.add_row(
                model, t["test"], f"{t['tps_mean']:.2f} ± {t['tps_stdev']:.2f}",
                " · ".join(notes) or "exact",
            )
        table = self.query_one("#results-table", DataTable)
        table.clear()
        table.display = not test_rows
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

    def _show_tool_results(self, report: dict, saved_path: Path | None = None) -> None:
        results = report.get("results", [])
        timestamp = report.get("timestamp", "")[:16].replace("T", " ")
        self.query_one("#results-title", Static).update(
            f"TOOL CALLING [dim]— {len(results)} model(s) · "
            f"{report.get('backend', '?')} · {timestamp}[/]"
        )
        table = self.query_one("#results-table", DataTable)
        table.clear()
        table.display = False
        tests_table = self.query_one("#tests-table", DataTable)
        tests_table.clear()
        tests_table.display = True
        errors = []
        for r in results:
            if r.get("error"):
                tests_table.add_row(r["model"], "—", "—", r["error"][:60])
                errors.append(f"{r['model']}: {r['error']}")
                continue
            lo, hi = r.get("ci95", [0, 0])
            tests_table.add_row(
                r["model"], "TOTAL",
                f"{r.get('passes', 0)}/{r.get('total', 0)}",
                f"{r.get('pass_rate', 0):.0%} · CI {lo:.0%}–{hi:.0%}",
            )
            for c in r.get("cases", []):
                mark = "✓" if c["passes"] == c["trials"] else ("±" if c["passes"] else "✗")
                note = "; ".join(c.get("fail_reasons", []))[:60] or c["checks"][:60]
                tests_table.add_row("", c["case"], f"{c['passes']}/{c['trials']} {mark}", note)
        self.query_one("#results-errors", Static).update(
            "[dim]" + " · ".join(errors) + "[/]" if errors else "")
        saved = self.query_one("#results-saved", Static)
        if saved_path is not None:
            saved.update(
                f"[$success]✓[/] report saved — JSON + Markdown in {saved_path.parent}"
            )
            self._header().set_context(f"saved: {saved_path.name}")
        else:
            saved.update("")
        self._set_stage("bench-results")
        self.query_one("#results-again", Button).focus()
