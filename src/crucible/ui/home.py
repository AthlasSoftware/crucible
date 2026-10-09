"""Home screen: the banner, machine monitor, and the main menu."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from crucible import __version__
from crucible.banner import BANNER_WIDTH, assemble
from crucible.report import list_reports
from crucible.servers import VllmServer
from crucible.sessions import DockerSandbox
from crucible.sessions.docker import HARNESS_LABEL, LABEL
from crucible.ui.widgets import ConfirmModal, MachineMonitor, ScreenHeader

# The killswitch. Only ever offered when CRUCIBLE itself is holding something —
# it touches containers carrying our own labels and nothing else on the host.
RELEASE_ID = "release"
_RELEASE_COLOR = "#ba3c5b"

# (id, shortcut, name, description). The shortcut replaces the decorative icon
# that used to sit here: it survives every font, and it teaches the keybinding
# instead of just taking up space. Sections group the menu by intent — measure,
# interact, everything else — with separator lines between them; a submenu was
# considered and rejected (an extra keystroke for two items buys nothing).
MENU_SECTIONS = [
    ("TEST", [
        ("benchmark", "b", "Benchmark", "measure model speed on this machine"),
        ("inspect", "x", "Inspect", "template & context footgun x-ray"),
    ]),
    ("RUN", [
        ("sessions", "s", "Session", "give a model a sandboxed shell"),
        ("harness", "h", "Harness", "chat with a model, spawn resources for it"),
    ]),
    ("MORE", [
        ("reports", "r", "Reports", "read past runs & transcripts"),
        ("resources", "e", "Resources", "pause/stop containers & check contention"),
        ("config", "c", "Config", "backends, models, sandbox"),
        ("about", "a", "About", "what is CRUCIBLE?"),
        ("quit", "q", "Quit", ""),
    ]),
]

_NAME_WIDTH = 12


def _option(oid: str, key: str, name: str, desc: str) -> Option:
    # Pad before wrapping in markup — padding a string that already contains
    # tags counts the tags as characters and the columns drift apart.
    padded = f"{name:<{_NAME_WIDTH}}"
    label = f"[$accent]{key}[/][bold]   {padded}[/]"
    if desc:
        label += f"[dim]{desc}[/]"
    return Option(label, id=oid)


def _menu_options(held: int) -> list:
    """The sectioned menu: dim headers, separator lines between groups, and the
    release entry pinned on top when CRUCIBLE is holding containers."""
    options: list = []
    if held:
        options.append(_release_option(held))
        options.append(None)
    for index, (header, entries) in enumerate(MENU_SECTIONS):
        if index or held:
            pass  # separators come from the None between sections below
        options.append(Option(f"[dim]{header}[/]", disabled=True))
        options.extend(_option(*entry) for entry in entries)
        if index < len(MENU_SECTIONS) - 1:
            options.append(None)
    return options


def _release_option(count: int) -> Option:
    padded = f"{'Release resources':<{_NAME_WIDTH}}"
    return Option(
        f"[{_RELEASE_COLOR}]![/][bold {_RELEASE_COLOR}]   {padded}[/]"
        f"[dim]free {count} container(s) CRUCIBLE spawned[/]",
        id=RELEASE_ID,
    )


class HomeScreen(Screen):
    BINDINGS = [
        ("b", "goto('benchmark')", "Benchmark"),
        ("s", "goto('sessions')", "Session"),
        ("h", "goto('harness')", "Harness"),
        ("x", "goto('inspect')", "Inspect"),
        ("r", "goto('reports')", "Reports"),
        ("e", "goto('resources')", "Resources"),
        ("c", "goto('config')", "Config"),
        ("a", "goto('about')", "About"),
        # Hidden from the footer: the global ctrl+q already advertises Quit
        # there, and listing it twice was pure noise.
        Binding("q", "app.quit", "Quit", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE", f"v{__version__}")
        yield Static(assemble(), id="banner")
        machine = MachineMonitor(id="machine")
        machine.border_title = "machine"
        yield machine
        yield OptionList(*_menu_options(0), id="menu")
        yield Static("", id="home-status")
        yield Static("↑↓ + enter · or press a key", id="home-hint")
        yield Footer()

    def on_mount(self) -> None:
        self._held = 0
        self.query_one("#menu", OptionList).focus()
        self._fit_banner()
        self._refresh_status()
        self._refresh_held()
        self.set_interval(5.0, self._refresh_held)

    def on_screen_resume(self) -> None:
        self._refresh_status()
        self._refresh_held()

    def _refresh_status(self) -> None:
        cfg = self.app.config
        endpoint = cfg.backend.endpoint()
        count = len(list_reports())
        tail = f"{count} reports" if count else "no runs yet — start with Benchmark"
        self.query_one("#home-status", Static).update(
            f"backend: {cfg.backend.active} @ {endpoint.base_url} · {tail}"
        )

    # ---- killswitch ----------------------------------------------------------

    @work(exclusive=True, group="home-held")
    async def _refresh_held(self) -> None:
        """Count what CRUCIBLE is holding, and offer the release option if any.

        Both labels: a session left running and a harness left attached are the
        same problem from the machine's point of view.
        """
        if self.app.screen is not self:
            return
        count = (await DockerSandbox.count_sessions(LABEL)
                 + await DockerSandbox.count_sessions(HARNESS_LABEL)
                 + await VllmServer.count())
        if count == self._held:
            return  # nothing changed — leave the menu (and its highlight) alone
        self._held = count
        self._rebuild_menu()

    def _rebuild_menu(self) -> None:
        menu = self.query_one("#menu", OptionList)
        # Rebuilding drops the highlight, so put the user back where they were.
        previous = None
        if menu.highlighted is not None:
            try:
                previous = menu.get_option_at_index(menu.highlighted).id
            except IndexError:
                previous = None
        menu.clear_options()
        options = _menu_options(self._held)
        menu.add_options(options)
        # Separators (None) are not options — track the option index separately
        # or every separator shifts the highlight one row down.
        target = None
        opt_index = -1
        for option in options:
            if option is None:
                continue
            opt_index += 1
            if option.disabled:
                continue
            if target is None:
                target = opt_index  # first selectable, the fallback
            if option.id == previous:
                target = opt_index
                break
        if target is not None:
            menu.highlighted = target

    def _release_resources(self) -> None:
        def confirmed(answer: bool | None) -> None:
            if answer:
                self._do_release()

        self.app.push_screen(
            ConfirmModal(
                f"Kill and remove all {self._held} container(s) CRUCIBLE spawned "
                "— sessions and harness resources alike? Anything still running "
                "dies; transcripts are kept. Nothing else on this host is touched.",
                yes_label="Release",
                no_label="Cancel",
            ),
            confirmed,
        )

    @work(group="home-release")
    async def _do_release(self) -> None:
        count = (await DockerSandbox.purge_stale(label=LABEL)
                 + await DockerSandbox.purge_stale(label=HARNESS_LABEL)
                 + await VllmServer.purge())
        self.notify(f"Released {count} container(s)")
        self._held = -1  # force the menu to rebuild even if the count is stale
        self._refresh_held()

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
        if target == RELEASE_ID:
            self._release_resources()
        elif target == "quit":
            self.app.action_quit_guarded()
        elif target:
            self.app.push_screen(target)
