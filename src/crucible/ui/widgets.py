"""Shared UI pieces: screen header, hardware bar, modals."""

from __future__ import annotations

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from crucible import hardware


class ScreenHeader(Horizontal):
    """One-line header: breadcrumb on the left, one live fact on the right."""

    def __init__(self, crumb: str, context: str = "", **kwargs):
        super().__init__(**kwargs)
        self._crumb_text = crumb
        self._context_text = context

    def compose(self) -> ComposeResult:
        yield Static(self._crumb_text, id="crumb")
        yield Static(self._context_text, id="context")

    def set_crumb(self, crumb: str) -> None:
        self._crumb_text = crumb
        self.query_one("#crumb", Static).update(crumb)

    def set_context(self, context: str) -> None:
        self._context_text = context
        self.query_one("#context", Static).update(context)


_SPARK = "▁▂▃▄▅▆▇█"
# Sized so the whole GPU line fits one 80-column row, badge included.
_HIST_LEN = 8
_VRAM_BAR = 10


class MachineMonitor(Static):
    """Live machine panel: hardware summary + per-GPU sparkline (utilization
    history) and VRAM meter, so you can see at a glance whether the cards are
    free. Probes in a thread every 2s while its screen is active.

    compact=True drops the static summary line — the form used under live
    benchmark/session views.
    """

    def __init__(self, *args, compact: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.compact = compact

    def on_mount(self) -> None:
        self._hist: dict[int, list[int]] = {}
        self.update("[dim]· probing machine …[/]")
        self._probe()
        self.set_interval(2.0, self._probe)

    @work(thread=True, exclusive=True, group="machine-mon")
    def _probe(self) -> None:
        if self.screen is not self.app.screen:
            return
        hw = hardware.probe()
        self.app.call_from_thread(self._render_hw, hw)

    @staticmethod
    def _spark(history: list[int]) -> str:
        pad = [0] * (_HIST_LEN - len(history)) + history
        return "".join(_SPARK[min(7, u * 8 // 101)] for u in pad)

    def _render_hw(self, hw: hardware.HardwareInfo) -> None:
        cpu = hw.cpu_model.replace(" Processor", "").replace("(R)", "").strip()
        lines = [] if self.compact else [
            f"[dim]{cpu} · {hw.cores_physical}c/{hw.cores_logical}t · "
            f"{hw.ram_total_gb:.0f}G RAM · {hw.os_name}[/]",
            "",
        ]
        if not hw.gpus:
            frac = hw.ram_used_gb / hw.ram_total_gb if hw.ram_total_gb else 0
            filled = round(frac * _VRAM_BAR)
            bar = "█" * filled + "[dim]░[/]" * (_VRAM_BAR - filled)
            lines.append(f"[dim]no NVIDIA GPU — cpu[/] {hw.load_pct:3.0f}%  "
                         f"[dim]ram[/] {bar} {hw.ram_used_gb:.1f}/{hw.ram_total_gb:.0f}G")
        for i, gpu in enumerate(hw.gpus):
            history = self._hist.setdefault(i, [])
            history.append(gpu.util_pct)
            del history[:-_HIST_LEN]
            name = gpu.name.replace("NVIDIA GeForce ", "")
            frac = gpu.vram_frac
            filled = round(frac * _VRAM_BAR)
            bar = "█" * filled + "[dim]░[/]" * (_VRAM_BAR - filled)
            status = ("[green]● free[/]" if gpu.idle
                      else "[#d7af5f]● busy[/]" if frac > 0.85 or gpu.util_pct > 80
                      else "[#5fafd7]● in use[/]")
            # Kept to one line at 80 columns: the status badge wrapping onto its
            # own line made a tidy panel look broken.
            lines.append(
                f"[bold]GPU{i}[/] [dim]{name}[/] "
                f"[#5fafd7]{self._spark(history)}[/]{gpu.util_pct:4d}% "
                f"[dim]vram[/] {bar} {gpu.vram_used_mb / 1024:4.1f}/"
                f"{gpu.vram_total_mb / 1024:.0f}G [dim]{gpu.temp_c}°C[/] {status}"
            )
            lines.extend(self._holder_lines(hw, gpu))
        self.update("\n".join(lines))

    def _holder_lines(self, hw: hardware.HardwareInfo, gpu: hardware.GpuInfo) -> list[str]:
        """Who is holding this card's VRAM — shown only while it is not idle.

        An idle card says nothing extra, so the panel keeps its usual height
        until something is actually in the way.
        """
        if gpu.idle:
            return []
        procs = hw.processes_on(gpu)
        if not procs:
            return ["      [dim]└ in use, no visible process (another user, or MIG)[/]"]
        if self.compact:
            # Under a running benchmark/session there is no room for a list.
            top = procs[0]
            extra = f" +{len(procs) - 1} more" if len(procs) > 1 else ""
            return [
                f"      [dim]└ {top.used_mb / 1024:.1f}G[/] {escape(top.label)}[dim]{extra}[/]"
            ]
        return [
            f"      [dim]└ {p.used_mb / 1024:4.1f}G[/] {escape(p.label)} [dim]pid {p.pid}[/]"
            for p in procs
        ]


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


class ApprovalModal(ModalScreen[str]):
    """The one interruption in the app: the model wants to run a command.

    Dims everything behind it, traps focus, and owns the y/n/a keys so they can
    never be swallowed by an input. Dismisses with "yes" | "no" | "auto".
    Initial focus is Deny — enter can never run a command the user hasn't read.
    """

    BINDINGS = [
        ("y", "answer('yes')", "Run it"),
        ("n", "answer('no')", "Deny"),
        ("a", "answer('auto')", "Auto"),
        ("escape", "answer('no')", "Deny"),
    ]

    def __init__(self, command: str, container: str):
        super().__init__()
        self.command = command
        self.container = container

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-box"):
            yield Static("⚠ APPROVE THIS COMMAND?", id="approval-title")
            yield Static(
                f"the model wants to run, inside {self.container}:", id="approval-context"
            )
            with VerticalScroll(id="approval-cmd-scroll"):
                yield Static(f"$ {escape(self.command)}", id="approval-cmd")
            with Horizontal(id="approval-buttons"):
                yield Button("y · Run it", variant="primary", id="approve-yes")
                yield Button("n · Deny", id="approve-no")
                yield Button("a · Auto from now on", variant="warning", id="approve-auto")
            yield Static(
                "deny tells the model to try another way · auto approves everything "
                "for the rest of this session",
                id="approval-help",
            )

    def on_mount(self) -> None:
        self.query_one("#approve-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        answers = {"approve-yes": "yes", "approve-no": "no", "approve-auto": "auto"}
        self.dismiss(answers.get(event.button.id or "", "no"))

    def action_answer(self, answer: str) -> None:
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
