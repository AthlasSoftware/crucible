"""Home screen: the banner and the main menu."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from crucible.banner import BANNER_WIDTH, assemble

MENU = [
    ("benchmark", "⚖  Benchmark — hardware & model speed"),
    ("sessions", "⚗  Sessions — LLM in a sandbox shell"),
    ("reports", "⌾  Reports — read past runs"),
    ("config", "⚙  Config — backends, models, sandbox"),
    ("about", "✦  About — what is CRUCIBLE?"),
    ("quit", "⏻  Quit"),
]


class HomeScreen(Screen):
    BINDINGS = [
        ("b", "goto('benchmark')", "Benchmark"),
        ("s", "goto('sessions')", "Sessions"),
        ("r", "goto('reports')", "Reports"),
        ("c", "goto('config')", "Config"),
        ("a", "goto('about')", "About"),
        ("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(assemble(), id="banner")
        yield OptionList(*[Option(label, id=oid) for oid, label in MENU], id="menu")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#menu", OptionList).focus()
        self._fit_banner()

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
