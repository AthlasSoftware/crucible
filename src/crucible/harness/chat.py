"""The harness loop: an ordinary conversation, with tools when resources exist.

Deliberately not the Session agent loop. That one takes a task, drives it to a
`DONE:` and stops; this one is a chat that happens to have hands. The model may
chain several tool calls inside one reply, but the turn always ends back with
you rather than at a completion marker — and it works fine with no resources
attached at all, which is the plain-chat case.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from crucible.backends.base import BackendError, LLMBackend, ToolCall, ToolsUnsupported
from crucible.config import HarnessCfg
from crucible.harness.resources import Resource, ResourceError
from crucible.sessions import protocol
from crucible.sessions.docker import SandboxError

# Turns kept verbatim; older ones fall out of the window (the system prompt and
# the first user message always survive).
HISTORY_CAP = 40

_BASE_PROMPT = """\
You are a model under evaluation inside CRUCIBLE, a local LLM testing ground.
You are talking to an engineer at their terminal. Be direct and concrete.

Answer normally when a question just needs an answer."""

_NO_RESOURCES = """

You currently have no tools. If a task would need a shell or a browser, say so —
the engineer can attach one and ask again."""

_WITH_RESOURCES = """

You have these resources, each with its own tools:

{resources}

Use them when they genuinely help; do not narrate a command you could simply
run. You may call several tools before replying. Tool output is fed straight
back to you and may be truncated for length. Everything runs in disposable
containers, not on the engineer's machine."""


class HarnessState(Enum):
    IDLE = "idle"
    THINKING = "thinking"
    AWAITING_APPROVAL = "awaiting approval"
    USING_TOOL = "using tool"


def _noop(*_args) -> None:
    pass


async def _auto_approve(_label: str) -> str:
    return "yes"


@dataclass
class HarnessCallbacks:
    on_state: Callable = _noop  # (HarnessState)
    on_chat_token: Callable = _noop  # (str) streamed assistant text
    on_assistant_done: Callable = _noop  # (str) full assistant text for the turn
    on_tool_call: Callable = _noop  # (name: str, args: dict)
    on_tool_result: Callable = _noop  # (name: str, result: str)
    on_output: Callable = _noop  # (stream: str, line: str) resource output
    on_info: Callable = _noop  # (str)
    on_error: Callable = _noop  # (str)
    # async (label) -> "yes" | "no" | "auto"; only used in approve-each mode
    request_approval: Callable = _auto_approve


@dataclass
class _Turn:
    text: str = ""
    tool_calls: list[ToolCall] | None = None


class HarnessChat:
    def __init__(
        self,
        backend: LLMBackend,
        model: str,
        cfg: HarnessCfg,
        resources: list[Resource] | None = None,
        callbacks: HarnessCallbacks | None = None,
    ):
        self.backend = backend
        self.model = model
        self.cfg = cfg
        self.resources: list[Resource] = resources if resources is not None else []
        self.cb = callbacks or HarnessCallbacks()
        self.auto = cfg.approval == "auto"
        self.tools_enabled = True
        self.state = HarnessState.IDLE
        self.messages: list[dict] = [{"role": "system", "content": self._system()}]
        self._stop = False

    # ---- UI-facing controls -------------------------------------------------

    def set_auto(self, auto: bool) -> None:
        self.auto = auto

    def request_stop(self) -> None:
        self._stop = True

    def sync_resources(self) -> None:
        """Re-state the system prompt after a resource is attached or dropped.

        Rewriting message 0 in place keeps the whole conversation intact: the
        model simply finds it has gained (or lost) hands mid-chat.
        """
        self.messages[0] = {"role": "system", "content": self._system()}

    # ---- internals ----------------------------------------------------------

    def _system(self) -> str:
        live = [r for r in self.resources if r.alive]
        if not live or not self.tools_enabled:
            return _BASE_PROMPT + _NO_RESOURCES
        listing = "\n".join(r.describe_for_model() for r in live)
        return _BASE_PROMPT + _WITH_RESOURCES.format(resources=listing)

    def _tools(self) -> list[dict] | None:
        if not self.tools_enabled:
            return None
        tools = [t for r in self.resources if r.alive for t in r.tools()]
        return tools or None

    def _resource_for(self, tool_name: str) -> Resource | None:
        return next((r for r in self.resources if r.alive and r.owns(tool_name)), None)

    def _set_state(self, state: HarnessState) -> None:
        self.state = state
        self.cb.on_state(state)

    def _context(self) -> list[dict]:
        if len(self.messages) <= HISTORY_CAP + 2:
            return self.messages
        head = self.messages[:2]
        tail = self.messages[-HISTORY_CAP:]
        # Never open the window on an orphaned tool result — servers reject a
        # tool message whose matching assistant call has scrolled away.
        while tail and tail[0].get("role") == "tool":
            tail = tail[1:]
        return head + tail

    def _drop_tool_messages(self) -> None:
        """Rewrite tool-protocol history as plain text, for a server that rejects it."""
        for i, msg in enumerate(self.messages):
            if msg.get("role") == "tool":
                self.messages[i] = {"role": "user", "content": msg.get("content", "")}
            elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                self.messages[i] = {"role": "assistant", "content": msg.get("content") or ""}

    async def _model_turn(self) -> _Turn:
        turn = _Turn(tool_calls=[])
        stream = self.backend.chat_stream(self.model, self._context(), tools=self._tools())
        async for event in stream:
            if self._stop:
                await stream.aclose()
                break
            if event.kind == "text":
                turn.text += event.text
                self.cb.on_chat_token(event.text)
            elif event.kind == "tool_call" and event.tool_call:
                assert turn.tool_calls is not None
                turn.tool_calls.append(event.tool_call)
        return turn

    # ---- public -------------------------------------------------------------

    async def send(self, user_text: str) -> None:
        """One user message, run until the model replies without calling tools."""
        self._stop = False
        self.messages.append({"role": "user", "content": user_text})
        try:
            await self._loop()
        except (SandboxError, ResourceError) as e:
            self.cb.on_error(f"resource: {e}")
        except BackendError as e:
            self.cb.on_error(str(e))
        finally:
            self._set_state(HarnessState.IDLE)

    async def _loop(self) -> None:
        for _ in range(max(1, self.cfg.max_tool_calls)):
            if self._stop:
                self.cb.on_info("stopped")
                return
            self._set_state(HarnessState.THINKING)
            try:
                turn = await self._model_turn()
            except ToolsUnsupported:
                if not self.tools_enabled:
                    raise
                self.cb.on_info(
                    "backend rejected tools — continuing as plain chat; "
                    "resources stay up but this model can't drive them"
                )
                self.tools_enabled = False
                self._drop_tool_messages()
                self.sync_resources()
                turn = await self._model_turn()
            if self._stop:
                self.cb.on_info("stopped")
                return

            visible = protocol.strip_think(turn.text)
            if turn.text:
                self.cb.on_assistant_done(turn.text)

            calls = turn.tool_calls or []
            self._append_assistant(visible, calls)
            if not calls:
                return

            for call in calls:
                if self._stop:
                    self.cb.on_info("stopped")
                    return
                await self._run_tool(call)
        self.cb.on_info(
            f"stopped after {self.cfg.max_tool_calls} tool calls in one turn "
            "— ask again to continue"
        )

    async def _run_tool(self, call: ToolCall) -> None:
        resource = self._resource_for(call.name)
        if resource is None:
            self.cb.on_info(f"model called unknown tool {call.name}")
            self._append_tool_result(
                call,
                f"error: no such tool '{call.name}'. Available: "
                + (", ".join(t["function"]["name"] for t in self._tools() or []) or "none"),
            )
            return

        self.cb.on_tool_call(call.name, call.arguments)
        if resource.needs_approval(call.name) and not self.auto:
            self._set_state(HarnessState.AWAITING_APPROVAL)
            answer = await self.cb.request_approval(_describe_call(call))
            if answer == "auto":
                self.auto = True
            elif answer != "yes" or self._stop:
                self._append_tool_result(
                    call, "The engineer declined this call. Suggest another approach."
                )
                return

        self._set_state(HarnessState.USING_TOOL)
        try:
            result = await resource.call(call.name, call.arguments, self.cb.on_output)
        except (SandboxError, ResourceError) as e:
            result = f"error: {e}"
        self.cb.on_tool_result(call.name, result)
        self._append_tool_result(call, result)

    # ---- history ------------------------------------------------------------

    def _append_assistant(self, text: str, calls: list[ToolCall]) -> None:
        msg: dict = {"role": "assistant", "content": text or None}
        if calls:
            msg["tool_calls"] = [
                {
                    "id": c.id or f"call_{i}_{len(self.messages)}",
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for i, c in enumerate(calls)
            ]
        self.messages.append(msg)

    def _append_tool_result(self, call: ToolCall, content: str) -> None:
        last = self.messages[-1]
        call_id = call.id
        if not call_id and last.get("tool_calls"):
            match = next(
                (tc for tc in last["tool_calls"] if tc["function"]["name"] == call.name), None
            )
            call_id = match["id"] if match else ""
        if self.tools_enabled and call_id:
            self.messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": content}
            )
        else:
            self.messages.append({"role": "user", "content": f"[{call.name}]\n{content}"})


def _describe_call(call: ToolCall) -> str:
    """A one-line rendering of a tool call for the approval modal."""
    if not call.arguments:
        return call.name
    if "command" in call.arguments:
        return f"{call.name}: {call.arguments['command']}"
    args = ", ".join(f"{k}={v!r}" for k, v in call.arguments.items())
    return f"{call.name}({args})"
