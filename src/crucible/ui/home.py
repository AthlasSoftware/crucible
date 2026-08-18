"""Home screen: the banner and the main menu."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from crucible import __version__
from crucible.banner import BANNER_WIDTH, assemble
from crucible.report import list_reports
from crucible.ui.widgets import ScreenHeader

MENU = [
    ("benchmark", "⚖  Benchmark", "measure model speed on this machine"),
    ("sessions", "⚗  Session", "give a model a sandboxed shell"),
    ("reports", "⌾  Reports", "read past runs & transcripts"),
    ("config", "⚙  Config", "backends · models · sandbox"),
    ("about", "✦  About", "what is CRUCIBLE?"),
    ("quit", "⏻  Quit", ""),
]


def _option(oid: str, name: str, desc: str) -> Option:
    label = f"{name:<14} [dim]{desc}[/]" if desc else name
    return Option(label, id=oid)


class HomeScreen(Screen):
    BINDINGS = [
        ("b", "goto('benchmark')", "Benchmark"),
        ("s", "goto('sessions')", "Session"),
        ("r", "goto('reports')", "Reports"),
        ("c", "goto('config')", "Config"),
        ("a", "goto('about')", "About"),
        ("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE", f"v{__version__}")
        yield Static(assemble(), id="banner")
        yield OptionList(*[_option(*m) for m in MENU], id="menu")
        yield Static("", id="home-status")
        yield Static("↑↓ + enter · or press a key", id="home-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#menu", OptionList).focus()
        self._fit_banner()
        self._refresh_status()

    def on_screen_resume(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        cfg = self.app.config
        endpoint = cfg.backend.endpoint()
        count = len(list_reports())
        tail = (
            f"{count} reports" if count
            else "no runs yet — start with ⚖ Benchmark"
        )
        self.query_one("#home-status", Static).update(
            f"backend: {cfg.backend.active} @ {endpoint.base_url} · {tail}"
        )

    def on_resize(self) -> None:
        self._fit_banner()

    def _fit_banner(self) -> None:
        banner = self.query_one("#banner", Static)
        banner.update(assemble(small=self.size.width <= BANNER_WIDTH + 2))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._activate(event.option.id or "")

    def action_goto(self, target: str) -> None:
        self._activate(target)

    def _activate(self, target: str) -> None:
        if target == "quit":
            self.app.action_quit_guarded()
        elif target:
            self.app.push_screen(target)
