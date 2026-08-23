"""Session screen: setup ▸ live workspace, with an unmissable approval modal."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from rich.markup import escape
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    ContentSwitcher,
    Footer,
    Input,
    Label,
    RichLog,
    Rule,
    Select,
    Static,
)

from crucible import render
from crucible.backends import BackendError, get_backend
from crucible.config import APPROVAL_MODES, BACKEND_NAMES
from crucible.report import SessionTranscript
from crucible.sessions import AgentCallbacks, AgentLoop, AgentState, DockerSandbox, protocol
from crucible.ui.widgets import ApprovalModal, ConfirmModal, MachineMonitor, ScreenHeader

_STREAM_TAIL = 300

_BADGES = {
    AgentState.IDLE: "[dim]● idle — type a task[/]",
    AgentState.THINKING: "[$primary]◐ thinking…[/]",
    AgentState.AWAITING_APPROVAL: "[bold $warning]⚠ AWAITING APPROVAL[/]",
    AgentState.EXECUTING: "[$accent]▶ executing…[/]",
}


class SessionScreen(Screen):
    _stage = "sess-setup"  # check_action can run before on_mount

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("c", "goto_config", "Config"),
        ("ctrl+p", "purge", "Purge stale containers"),
        ("ctrl+s", "stop_loop", "Stop task"),
        ("ctrl+t", "toggle_auto", "auto: off"),
        ("ctrl+d", "end_session", "End session"),
    ]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE ▸ Session ▸ setup")
        yield Rule()
        with ContentSwitcher(initial="sess-setup", id="sess-stages"):
            with Vertical(id="sess-setup", classes="hero"):
                yield Static("BACKEND & MODEL", classes="micro first")
                with Horizontal():
                    yield Label("backend")
                    yield Select([(n, n) for n in BACKEND_NAMES],
                                 id="sess-backend", allow_blank=False)
                    yield Label("model")
                    yield Select([], id="sess-model", prompt="pick a model…")
                yield Static("", id="sess-models-msg")
                yield Static("APPROVAL", classes="micro")
                with Horizontal():
                    yield Select([(m, m) for m in APPROVAL_MODES],
                                 id="sess-approval", allow_blank=False)
                    yield Static("", id="sess-approval-hint")
                yield Static("SANDBOX", classes="micro")
                yield Static("", id="sess-sandbox-summary")
                yield Static("change image / limits / network in Config (c)",
                             classes="step-hint")
                yield Button("▶  Start session — creates a disposable Docker container",
                             variant="primary", id="sess-start", classes="cta")
                yield Static(
                    "The model gets a real shell inside the container; nothing touches "
                    "your machine.\nA Markdown transcript is written live as you work.",
                    id="sess-explain",
                )
            with Vertical(id="sess-run"):
                with Horizontal(id="session-panes"):
                    yield RichLog(id="chat-log", markup=True, wrap=True)
                    yield RichLog(id="term-log", markup=True, wrap=True)
                yield Static("", id="stream-tail")
                yield Input(placeholder="Describe a task… (enter to send)",
                            id="task-input")
        yield MachineMonitor(id="session-hw", compact=True)
        yield Footer()

    def on_mount(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self._stage = "sess-setup"
        self.sandbox: DockerSandbox | None = None
        self.agent: AgentLoop | None = None
        self.backend = None
        self.transcript: SessionTranscript | None = None
        self._approval_future: asyncio.Future[str] | None = None
        self._approval_modal: ApprovalModal | None = None
        self._model_name = ""
        self._stream_buf = ""
        self._stream_ts = 0.0
        self.query_one("#chat-log", RichLog).border_title = "Conversation"
        self.query_one("#sess-backend", Select).value = self.app.config.backend.active
        self.query_one("#sess-approval", Select).value = self.app.config.sandbox.approval
        self._refresh_setup_texts()
        self._load_models()

    def on_screen_resume(self) -> None:
        if self._stage == "sess-setup":
            self._refresh_setup_texts()

    # ---- stages & header ----------------------------------------------------

    def _header(self) -> ScreenHeader:
        return self.query_one(ScreenHeader)

    def _set_stage(self, stage: str) -> None:
        self._stage = stage
        self.query_one("#sess-stages", ContentSwitcher).current = stage
        if stage == "sess-setup":
            self._header().set_crumb("CRUCIBLE ▸ Session ▸ setup")
            self._header().set_context(f"backend: {self._backend_name()}")
        else:
            name = self.sandbox.name if self.sandbox else "session"
            self._header().set_crumb(f"CRUCIBLE ▸ Session ▸ {name}")
        self.refresh_bindings()

    def check_action(self, action: str, parameters) -> bool | None:
        in_setup = self._stage == "sess-setup"
        if action in ("goto_config", "purge"):
            return in_setup
        if action == "stop_loop":
            return (not in_setup and self.agent is not None
                    and self.agent.state is not AgentState.IDLE)
        if action in ("toggle_auto", "end_session"):
            return not in_setup
        return True

    def _badge(self, state: AgentState) -> None:
        auto = " [dim]· auto[/]" if self.agent and self.agent.auto else ""
        self._header().set_context(
            f"{self._model_name} · {_BADGES.get(state, state.value)}{auto}"
        )

    # ---- setup stage --------------------------------------------------------

    def _refresh_setup_texts(self) -> None:
        cfg = self.app.config.sandbox
        self.query_one("#sess-sandbox-summary", Static).update(
            f"{cfg.image} · {cfg.cpus:g} CPUs · {cfg.memory} RAM · "
            f"network {'ON' if cfg.network else 'OFF'}"
        )
        self._refresh_approval_hint()
        self._header().set_context(f"backend: {self._backend_name()}")

    def _refresh_approval_hint(self) -> None:
        value = self.query_one("#sess-approval", Select).value
        hint = self.query_one("#sess-approval-hint", Static)
        if value == "auto":
            hint.update("[$warning]⚠[/] the model runs commands freely inside the sandbox")
            hint.add_class("caution")
        else:
            hint.update("you confirm every command before it runs (recommended)")
            hint.remove_class("caution")

    def _backend_name(self) -> str:
        value = self.query_one("#sess-backend", Select).value
        return str(value) if value != Select.BLANK else self.app.config.backend.active

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "sess-backend":
            self._header().set_context(f"backend: {self._backend_name()}")
            self._load_models()
        elif event.select.id == "sess-approval":
            self._refresh_approval_hint()
        elif event.select.id == "sess-model":
            self._update_cta()

    @work(exclusive=True, group="sess-models")
    async def _load_models(self) -> None:
        name = self._backend_name()
        model_select = self.query_one("#sess-model", Select)
        msg = self.query_one("#sess-models-msg", Static)
        msg.update(f"[dim]contacting {name}…[/]")
        backend = get_backend(self.app.config, name)
        endpoint = self.app.config.backend.endpoint(name)
        try:
            models = await backend.list_models()
        except BackendError:
            msg.update(
                f"[$error]✗[/] [dim]can't reach {name} at {endpoint.base_url} — "
                "is the server running? Fix the URL in Config (c)[/]"
            )
            return
        finally:
            await backend.close()
        msg.update("")
        options = [(m.id, m.id) for m in models]
        model_select.set_options(options)
        default = endpoint.default_model
        if default and any(v == default for _, v in options):
            model_select.value = default
        elif len(options) == 1:
            model_select.value = options[0][1]
        self._update_cta()
        if model_select.value != Select.BLANK:
            self.query_one("#sess-start", Button).focus()

    def _update_cta(self) -> None:
        button = self.query_one("#sess-start", Button)
        if self.query_one("#sess-model", Select).value == Select.BLANK:
            button.disabled = True
            button.label = "Pick a model first (step 1)"
        else:
            button.disabled = False
            button.label = "▶  Start session — creates a disposable Docker container"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sess-start":
            model = self.query_one("#sess-model", Select).value
            if model == Select.BLANK:
                self.notify("Pick a model first (step 1)", severity="warning")
                return
            self._start_session(self._backend_name(), str(model))

    @work(exclusive=True, group="sess-start")
    async def _start_session(self, backend_name: str, model: str) -> None:
        cfg = self.app.config
        start = self.query_one("#sess-start", Button)
        start.disabled = True
        start.label = "starting sandbox…"
        sandbox_cfg = replace(
            cfg.sandbox, approval=str(self.query_one("#sess-approval", Select).value)
        )
        sandbox = DockerSandbox(
            image=sandbox_cfg.image, cpus=sandbox_cfg.cpus,
            memory=sandbox_cfg.memory, network=sandbox_cfg.network,
        )
        try:
            await sandbox.create(on_output=self._on_output)
        except Exception as e:
            start.disabled = False
            self._update_cta()
            self.query_one("#sess-models-msg", Static).update(
                f"[$error]✗[/] [dim]sandbox failed: {escape(str(e))} — "
                "is Docker running?[/]"
            )
            return
        self.sandbox = sandbox
        self._model_name = model
        self.app.register_sandbox(sandbox)
        self.backend = get_backend(cfg, backend_name)
        self.transcript = SessionTranscript.create(
            sandbox.id, model, backend_name, sandbox_cfg.image, sandbox_cfg.network
        )
        self.agent = AgentLoop(
            self.backend, sandbox, model, sandbox_cfg,
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
        term = self.query_one("#term-log", RichLog)
        term.border_title = (
            f"Terminal · {sandbox_cfg.image} · net {'on' if sandbox_cfg.network else 'off'}"
        )
        self._set_stage("sess-run")
        self._chat(f"[green]✓[/] [dim]sandbox {sandbox.name} ready — "
                   f"{model} on {backend_name}[/]")
        self._chat('[dim]Describe a task below. Try: "install cowsay and draw a cow '
                   'saying hi"[/]')
        self._chat(f"[dim]transcript → {escape(str(self.transcript.path))}[/]")
        self._badge(AgentState.IDLE)
        task_input = self.query_one("#task-input", Input)
        task_input.focus()
        start.disabled = False
        self._update_cta()

    # ---- live stage ---------------------------------------------------------

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
        self._chat(f"[bold #87d7ff]you ▸[/] {escape(text)}")
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
        self._badge(state)
        self.refresh_bindings()

    def _on_chat_token(self, token: str) -> None:
        self._stream_buf = (self._stream_buf + token)[-_STREAM_TAIL:]
        now = time.monotonic()
        if now - self._stream_ts > 0.1:
            self._stream_ts = now
            tail = self._widget("#stream-tail", Static)
            if tail:
                tail.update("model ▸ " + escape(self._stream_buf.replace("\n", " ")))

    def _stream_clear(self) -> None:
        self._stream_buf = ""
        tail = self._widget("#stream-tail", Static)
        if tail:
            tail.update("")

    def _on_assistant_done(self, text: str) -> None:
        self._stream_clear()
        visible = protocol.strip_think(text)
        thoughts = len(text) - len(visible)
        if thoughts > 200:
            # Reasoning models flood the chat — fold the thinking into one line.
            self._chat(f"[dim italic]· thought for {thoughts} chars[/]")
        elif thoughts:
            self._chat(f"[dim italic]{escape(text.strip())}[/]")
        if visible:
            self._write_reply(visible)
        if self.transcript:
            self.transcript.assistant(visible or text.strip())

    def _write_reply(self, visible: str) -> None:
        """Speaker line plus the message, rendered as Markdown when it earns it."""
        markdown = self.app.config.ui.render_markdown
        if markdown and render.looks_like_markdown(visible):
            self._chat("[bold #5fafd7]model ▸[/]")
            log = self._widget("#chat-log", RichLog)
            if log:
                log.write(render.model_text(visible, markdown=True))
            return
        self._chat(f"[bold #5fafd7]model ▸[/] {escape(visible)}")

    def _on_command(self, command: str) -> None:
        self._chat(f"[bold #d7af5f]$ {escape(command)}[/]")
        term = self._widget("#term-log", RichLog)
        if term:  # echo the command like a real prompt would
            term.write(f"[bold #d7af5f]$ {escape(command)}[/]")
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
        self._chat(f"[bold red]✗ error:[/] {escape(error)}")
        if self.transcript:
            self.transcript.note(f"error: {error}")

    # ---- approval (async callback awaited by the agent loop) -----------------

    async def _request_approval(self, command: str) -> str:
        self._approval_future = asyncio.get_running_loop().create_future()
        self._approval_modal = ApprovalModal(
            command, self.sandbox.name if self.sandbox else "the sandbox"
        )
        self.app.push_screen(self._approval_modal, self._resolve_approval)
        self.app.bell()
        try:
            return await self._approval_future
        finally:
            self._approval_future = None
            self._approval_modal = None

    def _resolve_approval(self, answer: str | None) -> None:
        if answer == "auto":
            self._chat("[dim]auto mode ON — approving everything this session[/]")
        future = self._approval_future
        if future is not None and not future.done():
            future.set_result(answer or "no")

    def _dismiss_approval_modal(self) -> None:
        modal = self._approval_modal
        if modal is not None and modal.is_attached:
            modal.dismiss("no")

    # ---- actions ------------------------------------------------------------

    def action_goto_config(self) -> None:
        self.app.push_screen("config")

    def action_toggle_auto(self) -> None:
        if not self.agent:
            return
        self.agent.set_auto(not self.agent.auto)
        self._chat(f"[dim]auto mode {'ON' if self.agent.auto else 'OFF'}[/]")
        self._badge(self.agent.state)
        self.refresh_bindings()

    def action_stop_loop(self) -> None:
        if self.agent:
            self.agent.request_stop()
            self._dismiss_approval_modal()

    def action_end_session(self) -> None:
        if not (self.sandbox and self.sandbox.alive):
            self.notify("No live session")
            return

        def confirmed(answer: bool | None) -> None:
            if answer:
                self._destroy_session()

        self.app.push_screen(
            ConfirmModal(
                f"End session and delete container {self.sandbox.name}? "
                "The transcript is kept.",
                yes_label="End session",
                no_label="Keep running",
            ),
            confirmed,
        )

    @work(group="sess-destroy")
    async def _destroy_session(self) -> None:
        if self.agent:
            self.agent.request_stop()
            self._dismiss_approval_modal()
        if self.transcript:
            self.transcript.close()
            self.transcript = None
        if self.sandbox:
            await self.sandbox.destroy()
            self.app.unregister_sandbox(self.sandbox)
        if self.backend:
            await self.backend.close()
        self.sandbox = None
        self.agent = None
        self.backend = None
        for log_id in ("#chat-log", "#term-log"):
            log = self._widget(log_id, RichLog)
            if log:
                log.clear()
        self._set_stage("sess-setup")
        self._refresh_setup_texts()
        self._update_cta()

    @work(group="sess-purge")
    async def action_purge(self) -> None:
        live = {self.sandbox.name} if self.sandbox and self.sandbox.alive else set()
        count = await DockerSandbox.purge_stale(exclude=live)
        self.notify(f"Purged {count} stale session container(s)")
