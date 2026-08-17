"""CrucibleApp: screen registry, config, sandbox lifecycle, entry point."""

from __future__ import annotations

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
    CrucibleApp().run()


if __name__ == "__main__":
    main()
