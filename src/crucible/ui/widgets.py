"""Shared UI pieces."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from crucible import hardware


class HardwareBar(Static):
    """Self-refreshing one-line machine readout: CPU · RAM · per-GPU util/VRAM/temp.

    Probes in a thread (nvidia-smi can stall when GPUs are busy) on a 2s tick,
    but only while its screen is the active one.
    """

    def on_mount(self) -> None:
        self.update("[dim]· gathering hardware stats …[/]")
        self._probe()
        self.set_interval(2.0, self._probe)

    @work(thread=True, exclusive=True, group="hwbar")
    def _probe(self) -> None:
        if self.screen is not self.app.screen:
            return
        hw = hardware.probe()
        self.app.call_from_thread(self.update, f"[dim]{hardware.status_line(hw)}[/]")


class ConfirmModal(ModalScreen[bool]):
    """A yes/no question; dismisses with True/False."""

    BINDINGS = [
        ("y", "answer(True)", "Yes"),
        ("n", "answer(False)", "No"),
        ("escape", "answer(False)", "Cancel"),
    ]

    def __init__(self, question: str, yes_label: str = "Yes", no_label: str = "No"):
        super().__init__()
        self.question = question
        self.yes_label = yes_label
        self.no_label = no_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self.question)
            with Horizontal():
                yield Button(self.yes_label, variant="primary", id="confirm-yes")
                yield Button(self.no_label, id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)


def format_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1000
    return f"{size:.1f} TB"
