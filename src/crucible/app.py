"""CrucibleApp: screen registry, config, sandbox lifecycle, entry point.

`crucible` opens the full-screen TUI on a terminal; `crucible --inline` (or any
non-TTY invocation, e.g. piped) runs the scriptable inline CLI instead.
"""

from __future__ import annotations

import sys

from textual import work
from textual.app import App
from textual.binding import Binding

from crucible import __version__
from crucible.config import Config, load
from crucible.sessions import DockerSandbox
from crucible.ui.about import AboutScreen
from crucible.ui.benchmark_ui import BenchmarkScreen
from crucible.ui.config_ui import ConfigScreen
from crucible.ui.home import HomeScreen
from crucible.ui.reports_ui import ReportsScreen
from crucible.ui.session_ui import SessionScreen
from crucible.ui.widgets import ConfirmModal


class CrucibleApp(App):
    TITLE = "CRUCIBLE"
    SUB_TITLE = f"the LLM testing grounds · v{__version__}"
    CSS_PATH = "ui/theme.tcss"
    SCREENS = {
        "home": HomeScreen,
        "benchmark": BenchmarkScreen,
        "sessions": SessionScreen,
        "reports": ReportsScreen,
        "config": ConfigScreen,
        "about": AboutScreen,
    }
    BINDINGS = [
        Binding("ctrl+q", "quit_guarded", "Quit", priority=True),
        Binding("f1", "push_screen('about')", "About", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config: Config = load()
        self.live_sandboxes: list[DockerSandbox] = []

    def on_mount(self) -> None:
        self.push_screen("home")

    # ---- sandbox registry (for quit guard + cleanup) ------------------------

    def register_sandbox(self, sandbox: DockerSandbox) -> None:
        self.live_sandboxes.append(sandbox)

    def unregister_sandbox(self, sandbox: DockerSandbox) -> None:
        if sandbox in self.live_sandboxes:
            self.live_sandboxes.remove(sandbox)

    # ---- quit ----------------------------------------------------------------

    def action_quit_guarded(self) -> None:
        alive = [s for s in self.live_sandboxes if s.alive]
        if not alive:
            self._teardown_and_exit()
            return

        def confirmed(answer: bool | None) -> None:
            if answer:
                self._teardown_and_exit()

        self.push_screen(
            ConfirmModal(
                f"{len(alive)} live session container(s) will be destroyed. Quit?",
                yes_label="Quit",
                no_label="Stay",
            ),
            confirmed,
        )

    async def action_quit(self) -> None:  # ctrl+c / palette quit funnel here too
        self.action_quit_guarded()

    @work(group="shutdown")
    async def _teardown_and_exit(self) -> None:
        for sandbox in self.live_sandboxes:
            if sandbox.alive:
                await sandbox.destroy()
        self.exit()


def main() -> None:
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"crucible {__version__}")
        return
    # Inline mode: explicit flag, or automatically when there's no TTY (pipes/CI).
    if "--inline" in sys.argv or not (sys.stdin.isatty() and sys.stdout.isatty()):
        from crucible.cli.main import main as inline_main

        inline_main()
        return
    CrucibleApp().run()


if __name__ == "__main__":
    main()
