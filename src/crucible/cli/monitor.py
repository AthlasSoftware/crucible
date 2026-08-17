"""Live machine monitor: a one-line hardware readout pinned under the output
while a benchmark or session runs. Optionally hosts an extra renderable above
it (e.g. a rich Progress).

No-op on non-terminals (pipes/CI) so scripted runs stay clean.
"""

from __future__ import annotations

import asyncio
import contextlib

from rich.console import Group, RenderableType
from rich.live import Live
from rich.text import Text

from crucible import hardware
from crucible.cli.ui import BRAND, console

REFRESH_S = 1.5


def monitor_line(hw: hardware.HardwareInfo, status: str = "") -> Text:
    text = Text(no_wrap=True, overflow="ellipsis")
    if status:
        text.append("▸ ", style=f"bold {BRAND}")
        text.append(f"{status}   ", style=f"bold {BRAND}")
    text.append(hardware.status_line(hw), style="grey58")
    return text


class LiveMonitor:
    """Async context manager. While active, prints from the shared console are
    inserted above the pinned readout — printed lines must be complete lines.

    pause()/resume() release the terminal around interactive prompts
    (prompt_toolkit needs to own the tty while a question is up).
    """

    def __init__(self, extra: RenderableType | None = None, status: str = ""):
        self._extra = extra
        self._status = status
        self._hw: hardware.HardwareInfo | None = None
        self._enabled = console.is_terminal
        self._live: Live | None = None
        self._task: asyncio.Task | None = None

    def set_status(self, status: str) -> None:
        self._status = status
        self._refresh()

    def pause(self) -> None:
        if self._live:
            self._live.stop()

    def resume(self) -> None:
        if self._live:
            self._live.start(refresh=True)

    def _render(self) -> RenderableType:
        if self._hw is None:
            line: RenderableType = Text("· gathering hardware stats …", style="grey58")
        else:
            line = monitor_line(self._hw, self._status)
        if self._extra is not None:
            return Group(self._extra, line)
        return line

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    async def _tick(self) -> None:
        while True:
            self._hw = await asyncio.to_thread(hardware.probe)
            self._refresh()
            await asyncio.sleep(REFRESH_S)

    async def __aenter__(self) -> LiveMonitor:
        if self._enabled:
            self._live = Live(
                self._render(), console=console, refresh_per_second=4, transient=True
            )
            self._live.start()
            self._task = asyncio.create_task(self._tick())
        return self

    async def __aexit__(self, *exc) -> bool:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._live:
            self._live.stop()
            self._live = None
        return False
