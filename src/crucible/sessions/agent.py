"""The agent loop: an LLM driving a sandbox shell, one command per iteration.

States: IDLE → THINKING → (AWAITING_APPROVAL) → EXECUTING → THINKING ...
Exits to IDLE on DONE:, max iterations, backend error, or user stop.

Designed to run inside a Textual *async* worker (same event loop as the app),
so callbacks may touch widgets directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from crucible.backends.base import BackendError, LLMBackend, ToolCall, ToolsUnsupported
from crucible.config import SandboxCfg
from crucible.sessions import protocol
from crucible.sessions.docker import DockerSandbox, SandboxError

# Keep the conversation under small context windows (vLLM here runs 8k).
HISTORY_CAP = 30


class AgentState(Enum):
    IDLE = "idle"
    THINKING = "thinking"
    AWAITING_APPROVAL = "awaiting approval"
    EXECUTING = "executing"


def _noop(*_args) -> None:
    pass


@dataclass
class AgentCallbacks:
    on_state: Callable = _noop  # (AgentState)
    on_chat_token: Callable = _noop  # (str) streamed assistant text
    on_assistant_done: Callable = _noop  # (str) full assistant text for the turn
    on_command: Callable = _noop  # (str) proposed command
    on_output: Callable = _noop  # (stream: str, line: str) sandbox output
    on_mode_change: Callable = _noop  # ("native" | "fenced")
    on_done: Callable = _noop  # (summary: str)
    on_info: Callable = _noop  # (str) loop-level notices
    on_error: Callable = _noop  # (str)


@dataclass
class _Turn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class AgentLoop:
    def __init__(
        self,
        backend: LLMBackend,
        sandbox: DockerSandbox,
        model: str,
        cfg: SandboxCfg,
        callbacks: AgentCallbacks | None = None,
    ):
        self.backend = backend
        self.sandbox = sandbox
        self.model = model
        self.cfg = cfg
        self.cb = callbacks or AgentCallbacks()
        self.mode = "native"
        self.auto = cfg.approval == "auto"
        self.state = AgentState.IDLE
        self.messages: list[dict] = [{"role": "system", "content": self._system()}]
        self._approval = asyncio.Event()
        self._approved = False
        self._stop = False

    # ---- UI-facing controls -------------------------------------------------

    def approve(self) -> None:
        self._approved = True
        self._approval.set()

    def deny(self) -> None:
        self._approved = False
        self._approval.set()

    def set_auto(self, auto: bool) -> None:
        self.auto = auto
        if auto and self.state is AgentState.AWAITING_APPROVAL:
            self.approve()

    def request_stop(self) -> None:
        self._stop = True
        self._approval.set()  # unblock a pending approval wait

    # ---- internals ----------------------------------------------------------

    def _system(self) -> str:
        return protocol.system_prompt(
            self.mode, self.sandbox.image, self.sandbox.network, self.cfg.command_timeout_s
        )

    def _set_state(self, state: AgentState) -> None:
        self.state = state
        self.cb.on_state(state)

    def _switch_to_fenced(self) -> None:
        self.mode = "fenced"
        self.messages[0] = {"role": "system", "content": self._system()}
        # Native-protocol tool messages would 400 on a server without tool
        # support; rewrite any into plain user text.
        for i, msg in enumerate(self.messages):
            if msg.get("role") == "tool":
                self.messages[i] = {"role": "user", "content": msg.get("content", "")}
            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                self.messages[i] = {"role": "assistant", "content": msg.get("content") or ""}
        self.cb.on_mode_change(self.mode)

    def _context(self) -> list[dict]:
        """System + first user (the task) + most recent messages."""
        if len(self.messages) <= HISTORY_CAP + 2:
            return self.messages
        head = self.messages[:2]  # system + original task
        tail = self.messages[-HISTORY_CAP:]
        # Never let the window open on an orphaned tool result.
        while tail and tail[0].get("role") == "tool":
            tail = tail[1:]
        return head + tail

    async def _model_turn(self) -> _Turn:
        turn = _Turn()
        tools = [protocol.RUN_SHELL_TOOL] if self.mode == "native" else None
        stream = self.backend.chat_stream(self.model, self._context(), tools=tools)
        async for event in stream:
            if self._stop:
                await stream.aclose()
                break
            if event.kind == "text":
                turn.text += event.text
                self.cb.on_chat_token(event.text)
            elif event.kind == "tool_call" and event.tool_call:
                turn.tool_calls.append(event.tool_call)
        return turn

    async def run_task(self, user_text: str) -> None:
        """Run one task to completion. Called once per user instruction."""
        self._stop = False
        self.messages.append({"role": "user", "content": user_text})
        try:
            await self._loop()
        except SandboxError as e:
            self.cb.on_error(f"sandbox: {e}")
        except BackendError as e:
            self.cb.on_error(str(e))
        finally:
            self._set_state(AgentState.IDLE)

    async def _loop(self) -> None:
        for _ in range(self.cfg.max_iterations):
            if self._stop:
                self.cb.on_info("stopped by user")
                return
            self._set_state(AgentState.THINKING)
            try:
                turn = await self._model_turn()
            except ToolsUnsupported:
                self.cb.on_info("backend rejected tools — switching to fenced protocol")
                self._switch_to_fenced()
                turn = await self._model_turn()
            if self._stop:
                self.cb.on_info("stopped by user")
                return
            if turn.text:
                self.cb.on_assistant_done(turn.text)

            tool_call = turn.tool_calls[0] if turn.tool_calls else None
            command = None
            if tool_call:
                command = str(tool_call.arguments.get("command", "")) or None
            if command is None:
                command = protocol.parse_fenced(turn.text)
                tool_call = None  # command came from text, answer as user message

            self._append_assistant(turn, tool_call)

            if command is None:
                done = protocol.parse_done(turn.text)
                if done is not None:
                    self.cb.on_done(done)
                else:
                    # The model is talking to the user (question/answer); yield.
                    self.cb.on_info("model is waiting for your input")
                return

            if not await self._approved_to_run(command):
                if self._stop:
                    self.cb.on_info("stopped by user")
                    return
                self._append_result(tool_call, f"User declined to run: {command}. "
                                    "Suggest an alternative or finish with DONE:.")
                continue

            self._set_state(AgentState.EXECUTING)
            result = await self.sandbox.exec(
                command, timeout_s=self.cfg.command_timeout_s, on_output=self.cb.on_output
            )
            self._append_result(
                tool_call,
                protocol.format_exec_result(
                    result.exit_code, result.stdout, result.stderr, result.truncated
                ),
            )
        self.cb.on_info(f"reached max iterations ({self.cfg.max_iterations})")

    async def _approved_to_run(self, command: str) -> bool:
        self.cb.on_command(command)
        if self.auto:
            return True
        self._set_state(AgentState.AWAITING_APPROVAL)
        self._approval.clear()
        self._approved = False
        await self._approval.wait()
        return self._approved and not self._stop

    def _append_assistant(self, turn: _Turn, tool_call: ToolCall | None) -> None:
        msg: dict = {"role": "assistant", "content": turn.text or None}
        if tool_call:
            msg["tool_calls"] = [
                {
                    "id": tool_call.id or f"call_{len(self.messages)}",
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": _json_dumps(tool_call.arguments),
                    },
                }
            ]
        self.messages.append(msg)

    def _append_result(self, tool_call: ToolCall | None, content: str) -> None:
        if tool_call:
            last = self.messages[-1]
            call_id = last["tool_calls"][0]["id"] if last.get("tool_calls") else tool_call.id
            self.messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
        else:
            self.messages.append({"role": "user", "content": content})


def _json_dumps(obj: dict) -> str:
    import json

    return json.dumps(obj)
