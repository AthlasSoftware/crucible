"""Session screen: an LLM with its own sandboxed terminal."""

from __future__ import annotations

import asyncio
import time

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, RichLog, Select, Static

from crucible.backends import BackendError, get_backend
from crucible.config import BACKEND_NAMES
from crucible.report import SessionTranscript
from crucible.sessions import AgentCallbacks, AgentLoop, AgentState, DockerSandbox
from crucible.ui.widgets import ConfirmModal, HardwareBar

_STREAM_TAIL = 300


class SessionScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("y", "approve", "Approve"),
        ("n", "deny", "Deny"),
        ("a", "toggle_auto", "Auto"),
        ("ctrl+s", "stop_loop", "Stop"),
        ("ctrl+d", "destroy", "Destroy"),
        ("ctrl+p", "purge", "Purge stale"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("CRUCIBLE — Sessions", classes="screen-title")
        with Horizontal(id="session-setup"):
            yield Select([(n, n) for n in BACKEND_NAMES], id="sess-backend", allow_blank=False)
            yield Select([], id="sess-model", prompt="model…")
            yield Button("Start session", variant="warning", id="sess-start")
        with Horizontal(id="session-panes"):
            yield RichLog(id="chat-log", markup=True, wrap=True)
            yield RichLog(id="term-log", markup=True, wrap=True)
        yield Static("", id="stream-tail")
        yield Input(
            placeholder="Start a session, then describe the task… (enter to send)",
            id="task-input",
            disabled=True,
        )
        yield HardwareBar(id="session-hw")
        yield Static("no session", id="session-status")
        yield Footer()

    def on_mount(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self.sandbox: DockerSandbox | None = None
        self.agent: AgentLoop | None = None
        self.backend = None
        self.transcript: SessionTranscript | None = None
        self._approval_future: asyncio.Future[str] | None = None
        self._stream_buf = ""
        self._stream_ts = 0.0
        self.query_one("#chat-log", RichLog).border_title = "chat"
        self.query_one("#term-log", RichLog).border_title = "terminal"
        self.query_one("#sess-backend", Select).value = self.app.config.backend.active
        self._load_models()

    # ---- setup --------------------------------------------------------------

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "sess-backend":
            self._load_models()

    @work(exclusive=True, group="sess-models")
    async def _load_models(self) -> None:
        name = self._backend_name()
        model_select = self.query_one("#sess-model", Select)
        backend = get_backend(self.app.config, name)
        try:
            models = await backend.list_models()
        except BackendError as e:
            self._set_status(f"cannot list models: {e}")
            return
        finally:
            await backend.close()
        options = [(m.id, m.id) for m in models]
        model_select.set_options(options)
        default = self.app.config.backend.endpoint(name).default_model
        if default and any(v == default for _, v in options):
            model_select.value = default
        elif len(options) == 1:
            model_select.value = options[0][1]

    def _backend_name(self) -> str:
        value = self.query_one("#sess-backend", Select).value
        return str(value) if value != Select.BLANK else self.app.config.backend.active

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sess-start":
            model = self.query_one("#sess-model", Select).value
            if model == Select.BLANK:
                self.notify("Pick a model first", severity="warning")
                return
            if self.sandbox and self.sandbox.alive:
                self.notify("Session already running — destroy it first (ctrl+d)",
                            severity="warning")
                return
            self._start_session(self._backend_name(), str(model))

    @work(exclusive=True, group="sess-start")
    async def _start_session(self, backend_name: str, model: str) -> None:
        cfg = self.app.config
        term = self.query_one("#term-log", RichLog)
        self._set_status("starting sandbox…")
        sandbox = DockerSandbox(
            image=cfg.sandbox.image,
            cpus=cfg.sandbox.cpus,
            memory=cfg.sandbox.memory,
            network=cfg.sandbox.network,
        )
        try:
            await sandbox.create(on_output=self._on_output)
        except Exception as e:
            self._set_status("no session")
            term.write(f"[red]sandbox failed: {escape(str(e))}[/]")
            return
        self.sandbox = sandbox
        self.app.register_sandbox(sandbox)
        self.backend = get_backend(cfg, backend_name)
        self.transcript = SessionTranscript.create(
            sandbox.id, model, backend_name, cfg.sandbox.image, cfg.sandbox.network
        )
        self.agent = AgentLoop(
            self.backend,
            sandbox,
            model,
            cfg.sandbox,
            AgentCallbacks(
                on_state=self._on_state,
                on_chat_token=self._on_chat_token,
                on_assistant_done=self._on_assistant_done,
                on_command=self._on_command,
                on_output=self._on_output,
                on_mode_change=self._on_mode_change,
                on_done=self._on_done,
                on_info=self._on_info,
                on_error=self._on_error,
                request_approval=self._request_approval,
            ),
        )
        task_input = self.query_one("#task-input", Input)
        task_input.disabled = False
        task_input.placeholder = "Describe the task… (enter to send)"
        task_input.focus()
        self._chat(f"[dim]session {sandbox.id} — {model} on {backend_name}, "
                   f"image {cfg.sandbox.image}[/]")
        self._chat(f"[dim]transcript → {escape(str(self.transcript.path))}[/]")
        self._on_state(AgentState.IDLE)

    # ---- chat flow ----------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "task-input":
            return
        text = event.value.strip()
        if not text:
            return
        if not self.agent:
            self.notify("Start a session first", severity="warning")
            return
        if self.agent.state is not AgentState.IDLE:
            self.notify("Agent is busy — stop it first (ctrl+s)", severity="warning")
            return
        event.input.value = ""
        self._chat(f"[bold cyan]you ▸[/] {escape(text)}")
        if self.transcript:
            self.transcript.user(text)
        self._run_task(text)

    @work(group="sess-agent")
    async def _run_task(self, text: str) -> None:
        assert self.agent is not None
        await self.agent.run_task(text)

    # ---- agent callbacks ----------------------------------------------------
    # Callbacks may fire while the screen is being torn down (stop/quit mid-run);
    # a missing widget must never crash the agent worker, hence _widget().

    def _widget(self, selector: str, cls):
        try:
            return self.query_one(selector, cls)
        except NoMatches:
            return None

    def _chat(self, markup: str) -> None:
        chat = self._widget("#chat-log", RichLog)
        if chat:
            chat.write(markup)

    def _on_state(self, state: AgentState) -> None:
        self._stream_clear()
        hints = {
            AgentState.IDLE: "idle — type a task",
            AgentState.THINKING: "thinking…",
            AgentState.AWAITING_APPROVAL: "[bold]y[/] approve · [bold]n[/] deny · [bold]a[/] auto",
            AgentState.EXECUTING: "executing…",
        }
        self._set_status(hints.get(state, state.value))

    def _on_chat_token(self, token: str) -> None:
        self._stream_buf = (self._stream_buf + token)[-_STREAM_TAIL:]
        now = time.monotonic()
        if now - self._stream_ts > 0.1:
            self._stream_ts = now
            tail = self._widget("#stream-tail", Static)
            if tail:
                tail.update(escape(self._stream_buf.replace("\n", " ")))

    def _stream_clear(self) -> None:
        self._stream_buf = ""
        tail = self._widget("#stream-tail", Static)
        if tail:
            tail.update("")

    def _on_assistant_done(self, text: str) -> None:
        self._stream_clear()
        self._chat(f"[bold magenta]model ▸[/] {escape(text.strip())}")
        if self.transcript:
            self.transcript.assistant(text.strip())

    def _on_command(self, command: str) -> None:
        self._chat(f"[bold yellow]$ {escape(command)}[/]")
        if self.transcript:
            self.transcript.command(command)

    def _on_output(self, stream: str, line: str) -> None:
        term = self._widget("#term-log", RichLog)
        if term:
            if stream == "stderr":
                term.write(f"[red]{escape(line)}[/]")
            elif stream == "info":
                term.write(f"[dim]{escape(line)}[/]")
            else:
                term.write(escape(line))
        if self.transcript:
            self.transcript.output(stream, line)

    def _on_mode_change(self, mode: str) -> None:
        self._chat(f"[dim]protocol switched to {mode} mode[/]")
        if self.transcript:
            self.transcript.note(f"protocol switched to {mode} mode")

    def _on_done(self, summary: str) -> None:
        self._chat(f"[bold green]✓ DONE:[/] {escape(summary)}")
        if self.transcript:
            self.transcript.done(summary)

    def _on_info(self, info: str) -> None:
        self._chat(f"[dim]{escape(info)}[/]")
        if self.transcript:
            self.transcript.note(info)

    def _on_error(self, error: str) -> None:
        self._chat(f"[bold red]error:[/] {escape(error)}")
        if self.transcript:
            self.transcript.note(f"error: {error}")

    # ---- approval (async callback awaited by the agent loop) -----------------

    async def _request_approval(self, command: str) -> str:
        self._approval_future = asyncio.get_running_loop().create_future()
        try:
            return await self._approval_future
        finally:
            self._approval_future = None

    def _resolve_approval(self, answer: str) -> None:
        future = self._approval_future
        if future is not None and not future.done():
            future.set_result(answer)

    def _set_status(self, text: str) -> None:
        agent = self.agent
        prefix = ""
        if self.sandbox and self.sandbox.alive:
            mode = f" · {agent.mode}" if agent else ""
            auto = " · auto" if agent and agent.auto else ""
            prefix = f"[{self.sandbox.name}{mode}{auto}]  "
        status = self._widget("#session-status", Static)
        if status:
            status.update(f"{prefix}{text}")

    # ---- actions ------------------------------------------------------------

    def action_approve(self) -> None:
        if self.agent and self.agent.state is AgentState.AWAITING_APPROVAL:
            self._resolve_approval("yes")

    def action_deny(self) -> None:
        if self.agent and self.agent.state is AgentState.AWAITING_APPROVAL:
            self._resolve_approval("no")

    def action_toggle_auto(self) -> None:
        if not self.agent:
            return
        if self.agent.state is AgentState.AWAITING_APPROVAL:
            self._resolve_approval("auto")
            self._chat("[dim]auto mode ON[/]")
        else:
            self.agent.set_auto(not self.agent.auto)
            self._chat(f"[dim]auto mode {'ON' if self.agent.auto else 'OFF'}[/]")
            self._on_state(self.agent.state)

    def action_stop_loop(self) -> None:
        if self.agent:
            self.agent.request_stop()
            self._resolve_approval("no")  # unblock a pending approval wait

    def action_destroy(self) -> None:
        if not (self.sandbox and self.sandbox.alive):
            self.notify("No live session")
            return

        def confirmed(answer: bool | None) -> None:
            if answer:
                self._destroy_session()

        self.app.push_screen(
            ConfirmModal(f"Destroy container {self.sandbox.name}?", yes_label="Destroy"),
            confirmed,
        )

    @work(group="sess-destroy")
    async def _destroy_session(self) -> None:
        if self.agent:
            self.agent.request_stop()
            self._resolve_approval("no")
        if self.transcript:
            self.transcript.close()
            self.transcript = None
        if self.sandbox:
            await self.sandbox.destroy()
            self.app.unregister_sandbox(self.sandbox)
            self._chat(f"[dim]session {self.sandbox.id} destroyed[/]")
        if self.backend:
            await self.backend.close()
        self.sandbox = None
        self.agent = None
        self.backend = None
        self.query_one("#task-input", Input).disabled = True
        self._set_status("no session")

    @work(group="sess-purge")
    async def action_purge(self) -> None:
        live = {self.sandbox.id} if self.sandbox and self.sandbox.alive else set()
        count = await DockerSandbox.purge_stale(exclude=live)
        self.notify(f"Purged {count} stale session container(s)")
