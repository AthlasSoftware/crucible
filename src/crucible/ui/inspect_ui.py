"""Inspect screen: template & context X-ray for one model."""

from __future__ import annotations

from datetime import datetime

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Label, Rule, Select, Static

from crucible.backends import BackendError, get_backend
from crucible.benchmark.inspect import inspect_model, render_inspect_md
from crucible.benchmark.store import benchmarks_dir
from crucible.config import BACKEND_NAMES
from crucible.ui.widgets import ScreenHeader

_SEV_MARKUP = {
    "ok": "[green]✓[/]",
    "warn": "[#d7af5f]⚠[/]",
    "bad": "[red]✗[/]",
    "info": "[dim]·[/]",
}


class InspectScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("x", "run_xray", "Run x-ray"),
    ]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE ▸ Inspect")
        yield Rule()
        with Vertical(id="inspect-body", classes="hero"):
            yield Static("MODEL", classes="micro first")
            with Horizontal(classes="field-row"):
                yield Label("backend")
                yield Select([(n, n) for n in BACKEND_NAMES],
                             id="insp-backend", allow_blank=False)
                yield Label("model")
                yield Select([], id="insp-model", prompt="pick a model…")
                yield Button("Run x-ray", variant="primary", id="insp-run")
            yield Static(
                "template & context footguns: silent truncation (num_ctx), missing "
                "tool/system sections, double-BOS, live tool-acceptance probe",
                classes="step-hint",
            )
            yield Static("FINDINGS", classes="micro")
            with VerticalScroll(id="insp-scroll"):
                yield Static("[dim]pick a model and run the x-ray[/]", id="insp-findings")
        yield Footer()

    def on_mount(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self.query_one("#insp-backend", Select).value = self.app.config.backend.active
        self._load_models()

    def _backend_name(self) -> str:
        value = self.query_one("#insp-backend", Select).value
        return str(value) if value != Select.BLANK else self.app.config.backend.active

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "insp-backend":
            self._load_models()

    @work(exclusive=True, group="insp-models")
    async def _load_models(self) -> None:
        name = self._backend_name()
        model_select = self.query_one("#insp-model", Select)
        backend = get_backend(self.app.config, name)
        try:
            models = await backend.list_models()
        except BackendError as e:
            self.query_one("#insp-findings", Static).update(
                f"[red]✗[/] [dim]can't reach {name}: {escape(str(e)[:120])}[/]")
            return
        finally:
            await backend.close()
        options = [(m.id, m.id) for m in models]
        model_select.set_options(options)
        default = self.app.config.backend.endpoint(name).default_model
        if default and any(v == default for _, v in options):
            model_select.value = default
        elif len(options) == 1:
            model_select.value = options[0][1]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "insp-run":
            self.action_run_xray()

    def action_run_xray(self) -> None:
        model = self.query_one("#insp-model", Select).value
        if model == Select.BLANK:
            self.notify("Pick a model first", severity="warning")
            return
        self._run(self._backend_name(), str(model))

    @work(exclusive=True, group="insp-run")
    async def _run(self, backend_name: str, model: str) -> None:
        out = self.query_one("#insp-findings", Static)
        out.update(f"[dim]x-raying {escape(model)} — probing template, context and "
                   "tool acceptance…[/]")
        self.query_one(ScreenHeader).set_context(f"{model} · running")
        backend = get_backend(self.app.config, backend_name)
        try:
            result = await inspect_model(backend, model)
        except BackendError as e:
            out.update(f"[red]✗[/] x-ray failed: {escape(str(e)[:200])}")
            return
        finally:
            await backend.close()

        lines = []
        prov = result.get("provenance", {})
        if prov:
            bits = [str(v) for k, v in prov.items() if k != "template_sha256"]
            lines.append(f"[dim]{escape(' · '.join(bits))}[/]")
            lines.append("")
        bad = warn = 0
        for f in result["findings"]:
            mark = _SEV_MARKUP.get(f["severity"], "·")
            bad += f["severity"] == "bad"
            warn += f["severity"] == "warn"
            line = f"{mark} [bold]{escape(f['title'])}[/]"
            if f.get("detail"):
                line += f"\n   [dim]{escape(f['detail'])}[/]"
            lines.append(line)
        if result.get("template"):
            lines.append("")
            lines.append(f"[dim]template sha {prov.get('template_sha256', '?')} — "
                         "full template in the saved report[/]")
        out.update("\n".join(lines))

        ts = datetime.now().astimezone().isoformat(timespec="seconds")
        safe_model = model.replace("/", "_").replace(":", "-")
        path = (benchmarks_dir()
                / f"{ts.replace(':', '-').replace('+', '_')}_inspect_{safe_model}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_inspect_md(result), encoding="utf-8")
        verdict = (f"[red]{bad} problem(s)[/] · " if bad else "") + (
            f"[#d7af5f]{warn} warning(s)[/]" if warn else "[green]clean[/]")
        self.query_one(ScreenHeader).set_context(f"{model} · {verdict} · saved")
        self.notify(f"X-ray saved to {path.name}")
