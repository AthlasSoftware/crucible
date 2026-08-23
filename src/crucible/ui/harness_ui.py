"""Harness screen: pick a model, talk to it, hand it resources as you go.

Setup is deliberately thinner than Session's — a harness needs nothing but a
model, and resources are attached later from the live view, because deciding
"this needs a browser" usually happens mid-conversation.
"""

from __future__ import annotations

import asyncio
import time

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
from crucible.harness import (
    RESOURCE_KINDS,
    HarnessCallbacks,
    HarnessChat,
    HarnessState,
    Resource,
    create_resource,
    next_name,
)
from crucible.report import SessionTranscript
from crucible.sessions import protocol
from crucible.sessions.docker import HARNESS_LABEL, DockerSandbox
from crucible.ui.vllm_control import VllmControl
from crucible.ui.widgets import ApprovalModal, ConfirmModal, MachineMonitor, ScreenHeader

_STREAM_TAIL = 300

_BADGES = {
    HarnessState.IDLE: "[dim]● idle — say something[/]",
    HarnessState.THINKING: "[$primary]◐ thinking…[/]",
    HarnessState.AWAITING_APPROVAL: "[bold $warning]⚠ AWAITING APPROVAL[/]",
    HarnessState.USING_TOOL: "[$accent]▶ using tool…[/]",
}

_KIND_BLURB = {
    "terminal": "a Linux shell in a disposable container",
    "browser": "headless Chromium with internet access (first spawn pulls ~3.6 GB)",
    "search": "web search via SearXNG — no captchas, no container",
}


class HarnessScreen(Screen):
    _stage = "harn-setup"  # check_action can run before on_mount

    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("c", "goto_config", "Config"),
        ("ctrl+s", "stop_loop", "Stop"),
        ("ctrl+t", "toggle_auto", "auto: off"),
        ("ctrl+r", "focus_resource", "Add resource"),
        ("ctrl+d", "end_harness", "End harness"),
    ]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE ▸ Harness ▸ setup")
        yield Rule()
        with ContentSwitcher(initial="harn-setup", id="harn-stages"):
            with Vertical(id="harn-setup", classes="hero"):
                yield Static("BACKEND & MODEL", classes="micro first")
                with Horizontal(classes="field-row"):
                    yield Label("backend")
                    yield Select([(n, n) for n in BACKEND_NAMES],
                                 id="harn-backend", allow_blank=False)
                    yield Label("model")
                    yield Select([], id="harn-model", prompt="pick a model…")
                yield Static("", id="harn-models-msg")
                yield Static("", id="vllm-status", classes="server-status")
                yield VllmControl(id="harn-vllm")
                yield Static("APPROVAL", classes="micro")
                with Horizontal(classes="field-row"):
                    yield Select([(m, m) for m in APPROVAL_MODES],
                                 id="harn-approval", allow_blank=False)
                    yield Static("", id="harn-approval-hint")
                yield Button("▶  Open harness — just chat, no containers yet",
                             variant="primary", id="harn-start", classes="cta")
                yield Static(
                    "A plain conversation to begin with. Attach a terminal or a browser "
                    "whenever the model needs one — they spawn on demand and die with "
                    "the harness. A Markdown transcript is written live.",
                    id="harn-explain",
                )
            with Vertical(id="harn-run"):
                with Horizontal(id="harness-panes"):
                    yield RichLog(id="harn-chat", markup=True, wrap=True)
                    yield RichLog(id="harn-term", markup=True, wrap=True)
                yield Static("", id="harn-resources")
                with Horizontal(id="harn-spawn-row"):
                    yield Label("attach")
                    yield Select([(f"{k} — {_KIND_BLURB[k]}", k) for k in RESOURCE_KINDS],
                                 id="harn-kind", prompt="a resource…")
                    yield Button("＋ spawn", id="harn-spawn")
                yield Static("", id="harn-stream")
                yield Input(placeholder="Talk to the model… (enter to send)",
                            id="harn-input")
        yield MachineMonitor(id="harness-hw", compact=True)
        yield Footer()

    def on_mount(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self._stage = "harn-setup"
        self.chat: HarnessChat | None = None
        self.backend = None
        self.resources: list[Resource] = []
        self.transcript: SessionTranscript | None = None
        self._approval_future: asyncio.Future[str] | None = None
        self._approval_modal: ApprovalModal | None = None
        self._model_name = ""
        self._stream_buf = ""
        self._stream_ts = 0.0
        self.query_one("#harn-chat", RichLog).border_title = "Conversation"
        self.query_one("#harn-term", RichLog).border_title = "Resource output"
        self.query_one("#harn-backend", Select).value = self.app.config.backend.active
        self.query_one("#harn-approval", Select).value = self.app.config.harness.approval
        self._refresh_approval_hint()
        self._sync_vllm_visibility()
        self._load_models()

    def _sync_vllm_visibility(self) -> None:
        """The spawn bar only makes sense for vLLM — Ollama manages its own models."""
        show = self._backend_name() == "vllm" and self.app.config.vllm_server.enabled
        self.query_one("#harn-vllm", VllmControl).display = show
        self.query_one("#vllm-status", Static).display = show

    def on_vllm_control_changed(self, event: VllmControl.Changed) -> None:
        """A server was spawned or stopped — the model list just changed."""
        event.stop()
        self._load_models()

    # ---- stages -------------------------------------------------------------

    def _header(self) -> ScreenHeader:
        return self.query_one(ScreenHeader)

    def _set_stage(self, stage: str) -> None:
        self._stage = stage
        self.query_one("#harn-stages", ContentSwitcher).current = stage
        if stage == "harn-setup":
            self._header().set_crumb("CRUCIBLE ▸ Harness ▸ setup")
            self._header().set_context(f"backend: {self._backend_name()}")
        else:
            self._header().set_crumb(f"CRUCIBLE ▸ Harness ▸ {self._model_name}")
        self.refresh_bindings()

    def check_action(self, action: str, parameters) -> bool | None:
        in_setup = self._stage == "harn-setup"
        if action == "goto_config":
            return in_setup
        if action == "stop_loop":
            return (not in_setup and self.chat is not None
                    and self.chat.state is not HarnessState.IDLE)
        if action in ("toggle_auto", "end_harness", "focus_resource"):
            return not in_setup
        return True

    def _badge(self, state: HarnessState) -> None:
        auto = " [dim]· auto[/]" if self.chat and self.chat.auto else ""
        count = len([r for r in self.resources if r.alive])
        kit = f" [dim]· {count} resource(s)[/]" if count else ""
        self._header().set_context(
            f"{self._model_name} · {_BADGES.get(state, state.value)}{auto}{kit}"
        )

    # ---- setup --------------------------------------------------------------

    def _backend_name(self) -> str:
        value = self.query_one("#harn-backend", Select).value
        return str(value) if value != Select.BLANK else self.app.config.backend.active

    def _refresh_approval_hint(self) -> None:
        value = self.query_one("#harn-approval", Select).value
        hint = self.query_one("#harn-approval-hint", Static)
        if value == "auto":
            hint.update("[$warning]⚠[/] the model uses its resources without asking")
            hint.add_class("caution")
        else:
            hint.update("you confirm each command, click or navigation (reads are free)")
            hint.remove_class("caution")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "harn-backend":
            self._header().set_context(f"backend: {self._backend_name()}")
            self._sync_vllm_visibility()
            self._load_models()
        elif event.select.id == "harn-approval":
            self._refresh_approval_hint()
        elif event.select.id == "harn-model":
            self._update_cta()

    @work(exclusive=True, group="harn-models")
    async def _load_models(self) -> None:
        name = self._backend_name()
        model_select = self.query_one("#harn-model", Select)
        msg = self.query_one("#harn-models-msg", Static)
        msg.update(f"[dim]contacting {name}…[/]")
        backend = get_backend(self.app.config, name)
        endpoint = self.app.config.backend.endpoint(name)
        try:
            models = await backend.list_models()
        except BackendError:
            control = self._widget("#harn-vllm", VllmControl)
            if control is not None and control.display and not control.running:
                # The spawn bar right below already says there is no server and
                # what to do about it; two messages saying it is just noise.
                msg.update("")
            else:
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
            self.query_one("#harn-start", Button).focus()

    def _update_cta(self) -> None:
        button = self.query_one("#harn-start", Button)
        blank = self.query_one("#harn-model", Select).value == Select.BLANK
        button.disabled = blank
        button.label = ("Pick a model first" if blank
                        else "▶  Open harness — just chat, no containers yet")

    # ---- buttons ------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "harn-start":
            model = self.query_one("#harn-model", Select).value
            if model == Select.BLANK:
                self.notify("Pick a model first", severity="warning")
                return
            self._open_harness(self._backend_name(), str(model))
        elif event.button.id == "harn-spawn":
            kind = self.query_one("#harn-kind", Select).value
            if kind == Select.BLANK:
                self.notify("Pick a resource kind first", severity="warning")
                return
            self._spawn_resource(str(kind))

    def _open_harness(self, backend_name: str, model: str) -> None:
        cfg = self.app.config
        harness_cfg = cfg.harness
        approval = str(self.query_one("#harn-approval", Select).value)
        from dataclasses import replace

        harness_cfg = replace(harness_cfg, approval=approval)
        self._model_name = model
        self.backend = get_backend(cfg, backend_name)
        self.transcript = SessionTranscript.create(
            f"harness-{int(time.time())}", model, backend_name, "no resources yet", False
        )
        self.chat = HarnessChat(
            self.backend, model, harness_cfg, self.resources,
            HarnessCallbacks(
                on_state=self._on_state,
                on_chat_token=self._on_chat_token,
                on_assistant_done=self._on_assistant_done,
                on_tool_call=self._on_tool_call,
                on_tool_result=self._on_tool_result,
                on_output=self._on_output,
                on_info=self._on_info,
                on_error=self._on_error,
                request_approval=self._request_approval,
            ),
        )
        self._set_stage("harn-run")
        self._chat(f"[green]✓[/] [dim]harness open — {model} on {backend_name}[/]")
        self._chat("[dim]No resources yet: this is a plain conversation. Attach a "
                   "terminal or browser below when the model needs one.[/]")
        self._chat(f"[dim]transcript → {escape(str(self.transcript.path))}[/]")
        self._refresh_resources()
        self._badge(HarnessState.IDLE)
        self.query_one("#harn-input", Input).focus()

    # ---- resources ----------------------------------------------------------

    def _refresh_resources(self) -> None:
        widget = self._widget("#harn-resources", Static)
        if not widget:
            return
        live = [r for r in self.resources if r.alive]
        if not live:
            widget.update("[dim]resources: none — the model has no tools yet[/]")
            return
        parts = [
            f"[bold #5fafd7]{r.name}[/] [dim]{escape(r.summary())}[/]" for r in live
        ]
        widget.update("[dim]resources:[/] " + "   ".join(parts))

    @work(group="harn-spawn")
    async def _spawn_resource(self, kind: str) -> None:
        button = self._widget("#harn-spawn", Button)
        if button:
            button.disabled = True
            button.label = f"spawning {kind}…"
        name = next_name(kind, self.resources)
        resource = create_resource(kind, name, self.app.config.harness)
        self._chat(f"[dim]spawning {kind} '{name}' …[/]")
        try:
            await resource.start(self._on_output)
        except Exception as e:
            self._chat(f"[bold red]✗ could not spawn {kind}:[/] {escape(str(e))}")
            await resource.stop()
        else:
            self.resources.append(resource)
            if resource.sandbox:
                # Same registry the Session screen uses, so the quit guard warns
                # about a live harness and tears its containers down on exit.
                self.app.register_sandbox(resource.sandbox)
            if self.chat:
                # The model is told about its new hands from the next turn on.
                self.chat.sync_resources()
            self._chat(
                f"[green]✓[/] [dim]{name} ready ({escape(resource.container_name)}) — "
                f"the model can use it from your next message[/]"
            )
            if self.transcript:
                self.transcript.note(f"attached resource {name} ({resource.summary()})")
        finally:
            if button:
                button.disabled = False
                button.label = "＋ spawn"
            self._refresh_resources()
            if self.chat:
                self._badge(self.chat.state)

    # ---- chat ---------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "harn-input":
            return
        text = event.value.strip()
        if not text:
            return
        if not self.chat:
            self.notify("Open the harness first", severity="warning")
            return
        if self.chat.state is not HarnessState.IDLE:
            self.notify("Model is busy — stop it first (ctrl+s)", severity="warning")
            return
        event.input.value = ""
        self._chat(f"[bold #87d7ff]you ▸[/] {escape(text)}")
        if self.transcript:
            self.transcript.user(text)
        self._send(text)

    @work(group="harn-chat")
    async def _send(self, text: str) -> None:
        assert self.chat is not None
        await self.chat.send(text)

    # ---- callbacks ----------------------------------------------------------

    def _widget(self, selector: str, cls):
        try:
            return self.query_one(selector, cls)
        except NoMatches:
            return None

    def _chat(self, markup: str) -> None:
        log = self._widget("#harn-chat", RichLog)
        if log:
            log.write(markup)

    def _on_state(self, state: HarnessState) -> None:
        self._stream_clear()
        self._badge(state)
        self.refresh_bindings()

    def _on_chat_token(self, token: str) -> None:
        self._stream_buf = (self._stream_buf + token)[-_STREAM_TAIL:]
        now = time.monotonic()
        if now - self._stream_ts > 0.1:
            self._stream_ts = now
            tail = self._widget("#harn-stream", Static)
            if tail:
                tail.update("model ▸ " + escape(self._stream_buf.replace("\n", " ")))

    def _stream_clear(self) -> None:
        self._stream_buf = ""
        tail = self._widget("#harn-stream", Static)
        if tail:
            tail.update("")

    def _on_assistant_done(self, text: str) -> None:
        self._stream_clear()
        visible = protocol.strip_think(text)
        thoughts = len(text) - len(visible)
        if thoughts > 200:
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
            # A block reply gets its own line for the speaker; a one-liner would
            # only be pushed away from its label for nothing.
            self._chat("[bold #5fafd7]model ▸[/]")
            log = self._widget("#harn-chat", RichLog)
            if log:
                log.write(render.model_text(visible, markdown=True))
            return
        self._chat(f"[bold #5fafd7]model ▸[/] {escape(visible)}")

    def _on_tool_call(self, name: str, args: dict) -> None:
        rendered = args.get("command") or ", ".join(f"{k}={v}" for k, v in args.items())
        self._chat(f"[bold #d7af5f]⚒ {escape(name)}[/] [dim]{escape(str(rendered))}[/]")
        if self.transcript:
            self.transcript.command(f"{name} {rendered}")

    def _on_tool_result(self, name: str, result: str) -> None:
        term = self._widget("#harn-term", RichLog)
        if term:
            head = result if len(result) <= 2000 else result[:2000] + "\n[… truncated …]"
            term.write(f"[bold #d7af5f]⚒ {escape(name)}[/]")
            term.write(escape(head))

    def _on_output(self, stream: str, line: str) -> None:
        term = self._widget("#harn-term", RichLog)
        if term:
            if stream == "stderr":
                term.write(f"[red]{escape(line)}[/]")
            elif stream == "info":
                term.write(f"[dim]{escape(line)}[/]")
            else:
                term.write(escape(line))
        if self.transcript:
            self.transcript.output(stream, line)

    def _on_info(self, info: str) -> None:
        self._chat(f"[dim]{escape(info)}[/]")
        if self.transcript:
            self.transcript.note(info)

    def _on_error(self, error: str) -> None:
        self._chat(f"[bold red]✗ error:[/] {escape(error)}")
        if self.transcript:
            self.transcript.note(f"error: {error}")

    # ---- approval -----------------------------------------------------------

    async def _request_approval(self, label: str) -> str:
        self._approval_future = asyncio.get_running_loop().create_future()
        self._approval_modal = ApprovalModal(label, "this harness")
        self.app.push_screen(self._approval_modal, self._resolve_approval)
        self.app.bell()
        try:
            return await self._approval_future
        finally:
            self._approval_future = None
            self._approval_modal = None

    def _resolve_approval(self, answer: str | None) -> None:
        if answer == "auto":
            self._chat("[dim]auto mode ON — approving everything in this harness[/]")
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

    def action_focus_resource(self) -> None:
        select = self._widget("#harn-kind", Select)
        if select:
            select.focus()

    def action_toggle_auto(self) -> None:
        if not self.chat:
            return
        self.chat.set_auto(not self.chat.auto)
        self._chat(f"[dim]auto mode {'ON' if self.chat.auto else 'OFF'}[/]")
        self._badge(self.chat.state)
        self.refresh_bindings()

    def action_stop_loop(self) -> None:
        if self.chat:
            self.chat.request_stop()
            self._dismiss_approval_modal()

    def action_end_harness(self) -> None:
        live = [r for r in self.resources if r.alive]
        if not self.chat:
            self.notify("No harness open")
            return
        question = (
            f"End the harness and destroy {len(live)} resource container(s)? "
            "The transcript is kept."
            if live else "End the harness? The transcript is kept."
        )
        def confirmed(answer: bool | None) -> None:
            if answer:
                self._teardown()

        self.app.push_screen(
            ConfirmModal(question, yes_label="End harness", no_label="Keep it"),
            confirmed,
        )

    @work(group="harn-teardown")
    async def _teardown(self) -> None:
        if self.chat:
            self.chat.request_stop()
            self._dismiss_approval_modal()
        for resource in self.resources:
            sandbox = resource.sandbox
            await resource.stop()
            if sandbox:
                self.app.unregister_sandbox(sandbox)
        self.resources.clear()
        # A vLLM we spawned holds the GPUs; ending the harness must free them.
        control = self._widget("#harn-vllm", VllmControl)
        if control:
            await control.shutdown()
        if self.transcript:
            self.transcript.close()
            self.transcript = None
        if self.backend:
            await self.backend.close()
        self.chat = None
        self.backend = None
        for log_id in ("#harn-chat", "#harn-term"):
            log = self._widget(log_id, RichLog)
            if log:
                log.clear()
        self._set_stage("harn-setup")
        self._update_cta()

    @work(group="harn-purge")
    async def action_purge(self) -> None:
        keep = {r.container_name for r in self.resources if r.alive}
        count = await DockerSandbox.purge_stale(exclude=keep, label=HARNESS_LABEL)
        self.notify(f"Purged {count} stale harness container(s)")
