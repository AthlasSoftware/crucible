"""The vLLM server bar: spawn a server for a model, or stop the one we spawned.

Only ever shown when the vllm backend is selected. vLLM fixes its model at
launch, so this is how you change it from inside CRUCIBLE: replace the server.
Both the Benchmark and Harness setups mount one and listen for Changed, which
is their cue to reload the model list.
"""

from __future__ import annotations

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, Input, Static

from crucible.config import Config
from crucible.servers import VllmServer, VllmServerError, gpu_blockers


class VllmControl(Horizontal):
    """Spawn/stop a CRUCIBLE-owned vLLM. Emits Changed when the server appears
    or goes away."""

    class Changed(Message):
        """The spawned server's state changed; reload whatever depends on it."""

        def __init__(self, base_url: str | None) -> None:
            super().__init__()
            self.base_url = base_url

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.server: VllmServer | None = None
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Input(placeholder="model id to serve, e.g. org/model-AWQ", id="vllm-model")
        yield Button("spawn", id="vllm-spawn")
        yield Button("stop", id="vllm-stop")

    def on_mount(self) -> None:
        cfg: Config = self.app.config
        self.query_one("#vllm-model", Input).value = cfg.backend.vllm.default_model
        self._render_state()

    # ---- state ---------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.server is not None and self.server.alive

    def _status(self) -> Static:
        return self.screen.query_one("#vllm-status", Static)

    def _render_state(self, message: str | None = None) -> None:
        stop = self.query_one("#vllm-stop", Button)
        spawn = self.query_one("#vllm-spawn", Button)
        stop.display = self.running
        spawn.disabled = self._busy or self.running
        stop.disabled = self._busy
        try:
            status = self._status()
        except Exception:
            return
        if message:
            status.update(message)
        elif self.running and self.server:
            status.update(
                f"[$success]●[/] serving [bold]{escape(self.server.model)}[/] "
                f"[dim]at {self.server.base_url} — stop it to switch model[/]"
            )
        else:
            blockers = gpu_blockers()
            if blockers:
                status.update(
                    "[$warning]●[/] [dim]GPUs busy: "
                    + escape("; ".join(blockers))
                    + " — free them first[/]"
                )
            else:
                status.update(
                    "[dim]no vLLM spawned by CRUCIBLE — enter a model id and spawn one[/]"
                )

    # ---- actions -------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "vllm-spawn":
            model = self.query_one("#vllm-model", Input).value.strip()
            if not model:
                self.app.bell()
                self._render_state("[$error]✗[/] [dim]enter a model id first[/]")
                return
            self._spawn(model)
        elif event.button.id == "vllm-stop":
            self._stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "vllm-model":
            event.stop()
            self.query_one("#vllm-spawn", Button).press()

    @work(exclusive=True, group="vllm-spawn")
    async def _spawn(self, model: str) -> None:
        cfg: Config = self.app.config
        self._busy = True
        self._render_state(f"[dim]starting vLLM for {escape(model)} — this takes minutes…[/]")
        server = VllmServer(model, cfg.vllm_server)

        def emit(_stream: str, line: str) -> None:
            self._render_state(f"[dim]{escape(line)}[/]")

        try:
            await server.start(emit)
        except VllmServerError as e:
            self._busy = False
            self.server = None
            # The refusal message is multi-line and worth reading in full.
            self._render_state(f"[$error]✗[/] {escape(str(e).splitlines()[0])}")
            self.app.notify(str(e), severity="error", timeout=12)
            self._render_state()
            return
        except Exception as e:  # docker missing, image pull failure, …
            self._busy = False
            self.server = None
            self._render_state(f"[$error]✗[/] [dim]{escape(str(e))}[/]")
            return
        self._busy = False
        self.server = server
        # Point the vllm backend at the server we just spawned, so every picker
        # and benchmark on this screen talks to it without further wiring.
        cfg.backend.vllm.base_url = server.base_url
        self._render_state()
        self.post_message(self.Changed(server.base_url))

    @work(exclusive=True, group="vllm-stop")
    async def _stop(self) -> None:
        if not self.server:
            return
        self._busy = True
        self._render_state("[dim]stopping vLLM…[/]")
        await self.server.destroy()
        self.server = None
        self._busy = False
        self._render_state()
        self.post_message(self.Changed(None))

    async def shutdown(self) -> None:
        """Destroy the server if we still own one (screen teardown / quit)."""
        if self.server:
            await self.server.destroy()
            self.server = None
