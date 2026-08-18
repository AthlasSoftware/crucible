"""Home screen: the banner, machine monitor, and the main menu."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Footer, OptionList, Static
from textual.widgets.option_list import Option

from crucible import __version__
from crucible.banner import BANNER_WIDTH, assemble
from crucible.report import list_reports
from crucible.sessions import DockerSandbox
from crucible.ui.widgets import ConfirmModal, MachineMonitor, ScreenHeader

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
        machine = MachineMonitor(id="machine")
        machine.border_title = "machine"
        yield machine
        yield OptionList(*[_option(*m) for m in MENU], id="menu")
        yield Button("", variant="error", id="home-purge")
        yield Static("", id="home-status")
        yield Static("↑↓ + enter · or press a key", id="home-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#home-purge", Button).display = False
        self.query_one("#menu", OptionList).focus()
        self._fit_banner()
        self._refresh_status()
        self._refresh_sessions()
        self.set_interval(5.0, self._refresh_sessions)

    def on_screen_resume(self) -> None:
        self._refresh_status()
        self._refresh_sessions()

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

    # ---- session containers (stuck-session escape hatch) ---------------------

    @work(exclusive=True, group="home-sessions")
    async def _refresh_sessions(self) -> None:
        if self.app.screen is not self:
            return
        count = await DockerSandbox.count_sessions()
        button = self.query_one("#home-purge", Button)
        button.display = count > 0
        if count:
            button.label = f"⚠  Purge {count} session container(s)"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "home-purge":
            return

        def confirmed(answer: bool | None) -> None:
            if answer:
                self._purge_sessions()

        self.app.push_screen(
            ConfirmModal(
                "Kill and remove ALL crucible session containers? "
                "Any running session dies; transcripts are kept.",
                yes_label="Purge",
                no_label="Cancel",
            ),
            confirmed,
        )

    @work(group="home-purge")
    async def _purge_sessions(self) -> None:
        count = await DockerSandbox.purge_stale()
        self.notify(f"Purged {count} session container(s)")
        self._refresh_sessions()

    # ---- banner & navigation -------------------------------------------------

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
