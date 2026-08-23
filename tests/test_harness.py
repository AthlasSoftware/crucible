"""Harness: tool routing, the chat loop, approval, and graceful degradation."""

import pytest

from crucible.backends.base import ChatEvent, ToolCall, ToolsUnsupported
from crucible.config import HarnessCfg
from crucible.harness import (
    HarnessCallbacks,
    HarnessChat,
    HarnessState,
    Resource,
    create_resource,
    next_name,
)


class FakeResource(Resource):
    """A resource with no container behind it — records what it was asked to do."""

    kind = "fake"

    def __init__(self, name="fake", cfg=None, read_only=()):
        super().__init__(name, cfg or HarnessCfg())
        self.calls: list[tuple[str, dict]] = []
        self._read_only = read_only
        self._alive = True

    @property
    def alive(self):
        return self._alive

    def tools(self):
        return [self._tool("do", "do a thing", {"x": {"type": "string"}}, ["x"])]

    def needs_approval(self, tool_name):
        return tool_name[len(self.name) + 1 :] not in self._read_only

    async def call(self, tool_name, args, emit=None):
        self.calls.append((tool_name, args))
        return f"did {args.get('x')}"

    async def stop(self):
        self._alive = False

    def summary(self):
        return "fake resource"

    def describe_for_model(self):
        return f"- `{self.name}` — a fake resource."


class FakeBackend:
    """Replays a scripted list of turns; records the tools it was offered."""

    name = "fake"

    def __init__(self, turns):
        self.turns = list(turns)
        self.seen_tools: list[list[str] | None] = []
        self.seen_messages: list[list[dict]] = []

    async def chat_stream(self, model, messages, tools=None):
        self.seen_tools.append(
            [t["function"]["name"] for t in tools] if tools else None
        )
        self.seen_messages.append([dict(m) for m in messages])
        for event in self.turns.pop(0):
            if isinstance(event, Exception):
                raise event
            yield event

    async def close(self):
        pass


def _text(s):
    return ChatEvent(kind="text", text=s)


def _call(name, args, cid="c1"):
    return ChatEvent(kind="tool_call", tool_call=ToolCall(id=cid, name=name, arguments=args))


def _chat(backend, resources=None, **cfg_kwargs):
    cfg = HarnessCfg(approval="auto", **cfg_kwargs)
    return HarnessChat(backend, "m", cfg, resources if resources is not None else [])


# ---- naming & routing ------------------------------------------------------


def test_next_name_numbers_duplicates():
    a = FakeResource("terminal")
    b = FakeResource("terminal2")
    assert next_name("terminal", []) == "terminal"
    assert next_name("terminal", [a]) == "terminal2"
    assert next_name("terminal", [a, b]) == "terminal3"


def test_tool_names_are_namespaced_per_instance():
    first = create_resource("terminal", "terminal", HarnessCfg())
    second = create_resource("terminal", "terminal2", HarnessCfg())
    names = [t["function"]["name"] for t in first.tools() + second.tools()]
    assert names == ["terminal_run", "terminal2_run"]
    assert len(set(names)) == len(names)
    assert first.owns("terminal_run") and not first.owns("terminal2_run")


# ---- the loop --------------------------------------------------------------


async def test_plain_chat_without_resources_sends_no_tools():
    backend = FakeBackend([[_text("hello")]])
    chat = _chat(backend)
    await chat.send("hi")
    assert backend.seen_tools == [None]
    assert chat.messages[-1]["content"] == "hello"
    assert chat.state is HarnessState.IDLE


async def test_tool_call_runs_and_result_is_fed_back():
    resource = FakeResource()
    backend = FakeBackend([[_call("fake_do", {"x": "1"})], [_text("done")]])
    chat = _chat(backend, [resource])
    await chat.send("go")

    assert resource.calls == [("fake_do", {"x": "1"})]
    # The tool result must reach the model as a `tool` message tied to the call id.
    second_turn = backend.seen_messages[1]
    tool_msg = second_turn[-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["content"] == "did 1"


async def test_several_tool_calls_in_one_reply_all_run():
    resource = FakeResource()
    backend = FakeBackend([
        [_call("fake_do", {"x": "a"}, "c1"), _call("fake_do", {"x": "b"}, "c2")],
        [_text("both done")],
    ])
    await _chat(backend, [resource]).send("go")
    assert [args["x"] for _, args in resource.calls] == ["a", "b"]


async def test_unknown_tool_is_reported_not_crashed():
    resource = FakeResource()
    backend = FakeBackend([[_call("ghost_do", {"x": "1"})], [_text("ok")]])
    chat = _chat(backend, [resource])
    infos = []
    chat.cb = HarnessCallbacks(on_info=infos.append)
    await chat.send("go")
    assert resource.calls == []
    assert any("unknown tool" in i for i in infos)
    assert "no such tool" in backend.seen_messages[1][-1]["content"]


async def test_max_tool_calls_bounds_a_runaway_turn():
    resource = FakeResource()
    # A model that only ever calls tools would otherwise never yield the turn.
    backend = FakeBackend([[_call("fake_do", {"x": str(i)})] for i in range(10)])
    chat = _chat(backend, [resource], max_tool_calls=3)
    infos = []
    chat.cb = HarnessCallbacks(on_info=infos.append)
    await chat.send("go")
    assert len(resource.calls) == 3
    assert any("3 tool calls" in i for i in infos)


# ---- approval --------------------------------------------------------------


async def test_denied_call_does_not_run_and_tells_the_model():
    resource = FakeResource()
    backend = FakeBackend([[_call("fake_do", {"x": "1"})], [_text("fine")]])
    cfg = HarnessCfg(approval="approve-each")
    chat = HarnessChat(backend, "m", cfg, [resource])

    async def deny(_label):
        return "no"

    chat.cb = HarnessCallbacks(request_approval=deny)
    await chat.send("go")
    assert resource.calls == []
    assert "declined" in backend.seen_messages[1][-1]["content"]


async def test_auto_answer_flips_the_mode_for_the_rest_of_the_chat():
    resource = FakeResource()
    backend = FakeBackend([
        [_call("fake_do", {"x": "a"}, "c1"), _call("fake_do", {"x": "b"}, "c2")],
        [_text("ok")],
    ])
    chat = HarnessChat(backend, "m", HarnessCfg(approval="approve-each"), [resource])
    asked = []

    async def approve_once(label):
        asked.append(label)
        return "auto"

    chat.cb = HarnessCallbacks(request_approval=approve_once)
    await chat.send("go")
    assert len(asked) == 1  # the second call no longer prompts
    assert len(resource.calls) == 2
    assert chat.auto is True


async def test_read_only_verbs_skip_approval():
    resource = FakeResource(read_only=("do",))
    backend = FakeBackend([[_call("fake_do", {"x": "1"})], [_text("ok")]])
    chat = HarnessChat(backend, "m", HarnessCfg(approval="approve-each"), [resource])
    asked = []

    async def record(label):
        asked.append(label)
        return "yes"

    chat.cb = HarnessCallbacks(request_approval=record)
    await chat.send("go")
    assert asked == []
    assert len(resource.calls) == 1


# ---- degradation -----------------------------------------------------------


async def test_backend_without_tool_support_falls_back_to_plain_chat():
    resource = FakeResource()
    backend = FakeBackend([[ToolsUnsupported("no tools")], [_text("plain answer")]])
    chat = _chat(backend, [resource])
    infos = []
    chat.cb = HarnessCallbacks(on_info=infos.append)
    await chat.send("hi")

    assert chat.tools_enabled is False
    assert backend.seen_tools == [["fake_do"], None]  # retried without tools
    assert any("plain chat" in i for i in infos)
    assert chat.messages[-1]["content"] == "plain answer"


async def test_system_prompt_tracks_attached_resources():
    chat = _chat(FakeBackend([]))
    assert "no tools" in chat.messages[0]["content"]

    chat.resources.append(FakeResource())
    chat.sync_resources()
    assert "`fake`" in chat.messages[0]["content"]
    # History is untouched — the model just gains hands mid-conversation.
    assert chat.messages[0]["role"] == "system"


async def test_dropping_a_resource_removes_its_tools():
    resource = FakeResource()
    chat = _chat(FakeBackend([]), [resource])
    assert chat._tools() is not None
    await resource.stop()
    chat.sync_resources()
    assert chat._tools() is None
    assert "no tools" in chat.messages[0]["content"]


async def test_stop_mid_turn_skips_the_remaining_tool_calls():
    """request_stop() applies to the turn in flight — the second call never runs."""
    backend = FakeBackend([
        [_call("fake_do", {"x": "a"}, "c1"), _call("fake_do", {"x": "b"}, "c2")],
        [_text("unreachable")],
    ])
    chat = _chat(backend, [])

    class StoppingResource(FakeResource):
        async def call(self, tool_name, args, emit=None):
            chat.request_stop()
            return await super().call(tool_name, args, emit)

    resource = StoppingResource()
    chat.resources.append(resource)
    chat.sync_resources()
    await chat.send("go")
    assert [args["x"] for _, args in resource.calls] == ["a"]


async def test_history_window_never_starts_on_an_orphan_tool_message():
    chat = _chat(FakeBackend([]))
    # A realistic long history: assistant tool-call / tool-result pairs. Cutting
    # it at a fixed depth can land on a tool message whose call has scrolled out.
    chat.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(40):
        chat.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function",
                            "function": {"name": "fake_do", "arguments": "{}"}}],
        })
        chat.messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "r"})

    context = chat._context()
    assert len(context) < len(chat.messages)  # the window actually trimmed
    assert context[:2] == chat.messages[:2]  # system + original task survive
    assert context[2]["role"] != "tool"


@pytest.mark.parametrize("kind", ["terminal", "browser"])
def test_real_resources_expose_unique_valid_tool_names(kind):
    resource = create_resource(kind, kind, HarnessCfg())
    names = [t["function"]["name"] for t in resource.tools()]
    assert names, f"{kind} exposes no tools"
    assert all(n.replace("_", "").replace("-", "").isalnum() for n in names)
    assert all(resource.owns(n) for n in names)
