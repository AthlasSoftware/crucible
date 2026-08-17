"""Benchmark screen: hardware panel, benchmark runner, and result history."""

from __future__ import annotations

import time

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    ProgressBar,
    Select,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
)
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
from crucible.ui.widgets import format_bytes

_STREAM_TAIL = 220


class BenchmarkScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Static("CRUCIBLE — Benchmark", classes="screen-title")
        with TabbedContent(initial="tab-hardware"):
            with TabPane("Hardware", id="tab-hardware"):
                yield DataTable(id="hw-table", cursor_type="none", zebra_stripes=True)
            with TabPane("Run", id="tab-run"):
                with Horizontal(id="bench-controls"):
                    yield Select(
                        [(n, n) for n in BACKEND_NAMES],
                        id="bench-backend",
                        allow_blank=False,
                    )
                    yield Button("Reload models", id="bench-reload")
                    yield Button("Start", variant="warning", id="bench-start")
                    yield Button("Stop", id="bench-stop", disabled=True)
                yield SelectionList(id="bench-models")
                with Horizontal(id="bench-params"):
                    yield Label("runs")
                    yield Input(id="bench-runs", type="integer")
                    yield Label("max tokens")
                    yield Input(id="bench-maxtok", type="integer")
                with Horizontal(id="bench-progress-row"):
                    yield ProgressBar(id="bench-progress", show_eta=False)
                yield Static("", id="bench-status")
                yield Static("", id="bench-stream")
                yield DataTable(id="bench-results", cursor_type="none", zebra_stripes=True)
            with TabPane("History", id="tab-history"):
                yield DataTable(id="history-table", cursor_type="row", zebra_stripes=True)
                yield DataTable(id="history-detail", cursor_type="none", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self._runner: BenchmarkRunner | None = None
        self._stream_buf = ""
        self._stream_ts = 0.0
        self._history_entries = []

        hw_table = self.query_one("#hw-table", DataTable)
        hw_table.add_columns("Component", "Details")
        results = self.query_one("#bench-results", DataTable)
        results.add_columns(
            "Model", "load s", "gen t/s", "min", "max", "ttft s", "prompt t/s", "tokens", "exact"
        )
        history = self.query_one("#history-table", DataTable)
        history.add_columns("When", "Backend", "Models", "gen t/s (mean)")
        detail = self.query_one("#history-detail", DataTable)
        detail.add_columns("Model", "load s", "gen t/s", "ttft s", "prompt t/s", "tokens")

        cfg = self.app.config
        self.query_one("#bench-backend", Select).value = cfg.backend.active
        self.query_one("#bench-runs", Input).value = str(cfg.benchmark.runs)
        self.query_one("#bench-maxtok", Input).value = str(cfg.benchmark.max_tokens)

        self._refresh_hardware()
        self.set_interval(2.0, self._refresh_hardware)
        self._load_models()
        self._refresh_history()

    # ---- hardware tab -------------------------------------------------------

    @work(thread=True, exclusive=True, group="hw-probe")
    def _refresh_hardware(self) -> None:
        """Probe in a thread — nvidia-smi can stall when GPUs are busy."""
        if self.app.screen is not self:
            return
        hw = hardware.probe()
        self.app.call_from_thread(self._render_hardware, hw)

    def _render_hardware(self, hw: hardware.HardwareInfo) -> None:
        table = self.query_one("#hw-table", DataTable)
        table.clear()
        table.add_row("OS", hw.os_name)
        table.add_row("CPU", hw.cpu_model)
        table.add_row(
            "Cores", f"{hw.cores_physical} physical / {hw.cores_logical} logical"
            f" @ {hw.freq_mhz:.0f} MHz — load {hw.load_pct:.0f}%"
        )
        table.add_row("RAM", f"{hw.ram_used_gb:.1f} / {hw.ram_total_gb:.1f} GB used")
        table.add_row("Disk /", f"{hw.disk_free_gb:.0f} GB free of {hw.disk_total_gb:.0f} GB")
        if not hw.gpus:
            table.add_row("GPU", "No NVIDIA GPU detected (nvidia-smi unavailable)")
        for i, gpu in enumerate(hw.gpus):
            table.add_row(
                f"GPU {i}",
                f"{gpu.name} — VRAM {gpu.vram_used_mb}/{gpu.vram_total_mb} MiB, "
                f"util {gpu.util_pct}%, {gpu.temp_c}°C, driver {gpu.driver}",
            )

    # ---- run tab ------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "bench-backend":
            self._load_models()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "bench-reload":
            self._load_models()
        elif event.button.id == "bench-start":
            self._start()
        elif event.button.id == "bench-stop":
            if self._runner:
                self._runner.cancel()
                self._status("stopping after the current generation…")

    def _backend_name(self) -> str:
        value = self.query_one("#bench-backend", Select).value
        return str(value) if value != Select.BLANK else self.app.config.backend.active

    @work(exclusive=True, group="bench-models")
    async def _load_models(self) -> None:
        name = self._backend_name()
        selection_list = self.query_one("#bench-models", SelectionList)
        selection_list.clear_options()
        self._status(f"loading models from {name}…")
        backend = get_backend(self.app.config, name)
        try:
            models = await backend.list_models()
        except BackendError as e:
            self._status(f"[red]{e}[/]")
            return
        finally:
            await backend.close()
        default = self.app.config.backend.endpoint(name).default_model
        for m in models:
            label = m.id if not m.size_bytes else f"{m.id}  ({format_bytes(m.size_bytes)})"
            selection_list.add_option(
                Selection(label, m.id, m.id == default or len(models) == 1)
            )
        self._status(f"{len(models)} model(s) on {name} — select and Start")

    def _start(self) -> None:
        models = self.query_one("#bench-models", SelectionList).selected
        if not models:
            self.notify("Select at least one model first", severity="warning")
            return
        try:
            runs = int(self.query_one("#bench-runs", Input).value or "3")
            max_tokens = int(self.query_one("#bench-maxtok", Input).value or "256")
        except ValueError:
            self.notify("runs/max tokens must be integers", severity="error")
            return
        params = BenchmarkParams(
            runs=max(1, runs),
            max_tokens=max(16, max_tokens),
            warmup=self.app.config.benchmark.warmup,
            prompt_set=self.app.config.benchmark.prompt_set,
        )
        self._run_benchmark(self._backend_name(), list(models), params)

    @work(exclusive=True, group="bench-run")
    async def _run_benchmark(self, name: str, models: list[str], params: BenchmarkParams) -> None:
        start_btn = self.query_one("#bench-start", Button)
        stop_btn = self.query_one("#bench-stop", Button)
        start_btn.disabled = True
        stop_btn.disabled = False
        results_table = self.query_one("#bench-results", DataTable)
        results_table.clear()
        progress = self.query_one("#bench-progress", ProgressBar)
        prompts = get_prompt_set(params.prompt_set)
        total = len(models) * params.runs * len(prompts)
        progress.update(total=total, progress=0)
        self._done_models = 0

        def on_progress(ev: dict) -> None:
            if ev["phase"] == "warmup":
                self._status(f"[{ev['model']}] warmup (loading model)…")
            elif ev["phase"] == "model-done":
                self._done_models += 1
            elif ev["token"] is not None:
                self._stream(ev["token"])
            else:
                overall = self._done_models * params.runs * len(prompts) + ev["done"]
                progress.update(progress=overall)
                self._status(
                    f"[{ev['model']}] {ev['done']}/{ev['total']} generations "
                    f"(prompt: {ev['prompt_id']})"
                )

        backend = get_backend(self.app.config, name)
        try:
            self._runner = BenchmarkRunner(
                backend, models, prompts, params, hardware.probe(), on_progress
            )
            report = await self._runner.run()
        except BackendError as e:
            self._status(f"[red]benchmark failed: {e}[/]")
            return
        finally:
            await backend.close()
            self._runner = None
            start_btn.disabled = False
            stop_btn.disabled = True
            self._stream_clear()

        for r in report["results"]:
            s = r["summary"]
            if r.get("error"):
                results_table.add_row(r["model"], "—", "—", "—", "—", "—", "—", "—", "error")
                self._status(f"[red]{r['model']}: {r['error']}[/]")
                continue
            results_table.add_row(
                r["model"],
                f"{r['load_s']:.1f}" if r["load_s"] else "—",
                f"{s['gen_tps']['mean']:.1f}",
                f"{s['gen_tps']['min']:.1f}",
                f"{s['gen_tps']['max']:.1f}",
                f"{s['ttft_s']['mean']:.2f}",
                f"{s['prompt_tps']['mean']:.0f}",
                str(s["total_gen_tokens"]),
                "≈" if s["approx"] else "✓",
            )
        path = save(report)
        progress.update(progress=total)
        self._status(f"done — saved to {path.name}")
        self._refresh_history()

    def _status(self, text: str) -> None:
        self.query_one("#bench-status", Static).update(text)

    def _stream(self, token: str) -> None:
        self._stream_buf = (self._stream_buf + token)[-_STREAM_TAIL:]
        now = time.monotonic()
        if now - self._stream_ts > 0.1:  # throttle redraws
            self._stream_ts = now
            self.query_one("#bench-stream", Static).update(self._stream_buf.replace("\n", " "))

    def _stream_clear(self) -> None:
        self._stream_buf = ""
        self.query_one("#bench-stream", Static).update("")

    # ---- history tab --------------------------------------------------------

    def _refresh_history(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.clear()
        self._history_entries = list_history()
        for e in self._history_entries:
            tps = ", ".join(f"{m}: {v:.1f}" for m, v in e.mean_gen_tps.items())
            table.add_row(e.timestamp, e.backend, ", ".join(e.models), tps)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "history-table":
            return
        index = event.cursor_row
        if not 0 <= index < len(self._history_entries):
            return
        report = load_report(self._history_entries[index].path)
        detail = self.query_one("#history-detail", DataTable)
        detail.clear()
        for r in report.get("results", []):
            s = r.get("summary", {})
            detail.add_row(
                r["model"],
                f"{r['load_s']:.1f}" if r.get("load_s") else "—",
                f"{s.get('gen_tps', {}).get('mean', 0):.1f}",
                f"{s.get('ttft_s', {}).get('mean', 0):.2f}",
                f"{s.get('prompt_tps', {}).get('mean', 0):.0f}",
                str(s.get("total_gen_tokens", 0)),
            )
