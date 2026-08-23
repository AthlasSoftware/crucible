"""Harness flow: chat with a model inline, attaching resources as you go.

`/attach terminal`, `/attach browser`, `/resources` and `/drop <name>` are the
inline equivalents of the TUI's spawn row — everything else you type is a
message to the model.
"""

from __future__ import annotations

import time

from rich.markup import escape

from crucible import render
from crucible.cli.common import pick_backend, pick_models
from crucible.cli.ui import confirm, console, rule, select, text
from crucible.config import Config
from crucible.harness import (
    RESOURCE_KINDS,
    HarnessCallbacks,
    HarnessChat,
    Resource,
    ResourceError,
    create_resource,
    next_name,
)
from crucible.report import SessionTranscript
from crucible.sessions import protocol
from crucible.sessions.docker import SandboxError

_HELP = (
    "[muted]commands:[/] /attach terminal · /attach browser · /resources · "
    "/drop <name> · /help · empty line to quit"
)


async def run(cfg: Config) -> None:
    rule("harness")
    backend_name = await pick_backend(cfg)
    if backend_name is None:
        return
    backend, models = await pick_models(cfg, backend_name, multi=False)
    if not models:
        await backend.close()
        return
    model = models[0]

    resources: list[Resource] = []
    transcript = SessionTranscript.create(
        f"harness-{int(time.time())}", model, backend_name, "no resources yet", False
    )
    console.print(f"[ok]⌘ harness open[/] [muted]— {model} @ {backend_name}[/]")
    console.print(f"[muted]transcript →[/] [accent]{transcript.path}[/]")
    console.print(_HELP)

    # Streaming raw tokens and printing a rendered message would show the reply
    # twice, so the two are alternatives: render Markdown at the end of the turn,
    # or watch the tokens arrive. Tool-call lines still report progress either way.
    render_md = cfg.ui.render_markdown
    streaming = False

    def end_stream() -> None:
        nonlocal streaming
        if streaming:
            console.print()
            streaming = False

    def on_token(token: str) -> None:
        nonlocal streaming
        if render_md:
            return
        streaming = True
        console.print(escape(token), end="", style="grey74")

    async def request_approval(label: str) -> str:
        end_stream()
        console.print(f"[cmd]⚒ {escape(label)}[/]")
        answer = await select(
            "Allow this?",
            [("allow", "yes"), ("deny", "no"), ("auto — allow everything from now on", "auto")],
            default="yes",
        )
        return answer or "no"

    def on_output(stream: str, line: str) -> None:
        end_stream()
        transcript.output(stream, line)
        style = {"stderr": "red", "info": "muted"}.get(stream, "")
        console.print("[grey30]│[/] " + (f"[{style}]{escape(line)}[/]" if style else escape(line)))

    def assistant_done(text_: str) -> None:
        end_stream()
        visible = protocol.strip_think(text_).strip()
        transcript.assistant(visible or text_.strip())
        if render_md and visible:
            console.print("[brand]model ▸[/]")
            console.print(render.model_text(visible, markdown=True))

    def on_tool_call(name: str, args: dict) -> None:
        end_stream()
        rendered = args.get("command") or ", ".join(f"{k}={v}" for k, v in args.items())
        transcript.command(f"{name} {rendered}")
        console.print(f"[cmd]⚒ {escape(name)}[/] [muted]{escape(str(rendered))}[/]")

    def on_info(info: str) -> None:
        end_stream()
        transcript.note(info)
        console.print(f"[muted]· {escape(info)}[/]")

    def on_error(error: str) -> None:
        end_stream()
        transcript.note(f"error: {error}")
        console.print(f"[err]✗ {escape(error)}[/]")

    chat = HarnessChat(
        backend, model, cfg.harness, resources,
        HarnessCallbacks(
            on_chat_token=on_token,
            on_assistant_done=assistant_done,
            on_tool_call=on_tool_call,
            on_output=on_output,
            on_info=on_info,
            on_error=on_error,
            request_approval=request_approval,
        ),
    )

    async def attach(kind: str) -> None:
        if kind not in RESOURCE_KINDS:
            console.print(f"[err]unknown resource {kind!r} — try {', '.join(RESOURCE_KINDS)}[/]")
            return
        name = next_name(kind, resources)
        resource = create_resource(kind, name, cfg.harness)
        try:
            with console.status(f"[muted]spawning {kind}…[/]", spinner="dots"):
                await resource.start(on_output)
        except (SandboxError, ResourceError, OSError) as e:
            console.print(f"[err]could not spawn {kind}: {escape(str(e))}[/]")
            await resource.stop()
            return
        resources.append(resource)
        chat.sync_resources()
        transcript.note(f"attached resource {name} ({resource.summary()})")
        console.print(f"[ok]✓ {name} ready[/] [muted]{escape(resource.summary())}[/]")

    async def drop(name: str) -> None:
        target = next((r for r in resources if r.name == name), None)
        if target is None:
            console.print(f"[err]no resource named {name!r}[/]")
            return
        await target.stop()
        resources.remove(target)
        chat.sync_resources()
        console.print(f"[muted]dropped {name}[/]")

    def show_resources() -> None:
        live = [r for r in resources if r.alive]
        if not live:
            console.print("[muted]no resources attached — the model has no tools[/]")
            return
        for r in live:
            console.print(f"  [accent]{r.name}[/] [muted]{escape(r.summary())} · "
                          f"{escape(r.container_name)}[/]")

    try:
        while True:
            console.print()
            line = (await text("you ▸")).strip()
            if not line:
                if await confirm("End the harness?"):
                    break
                continue
            if line.startswith("/"):
                parts = line[1:].split()
                command, args = (parts[0] if parts else ""), parts[1:]
                if command == "attach" and args:
                    await attach(args[0])
                elif command == "drop" and args:
                    await drop(args[0])
                elif command == "resources":
                    show_resources()
                elif command == "help":
                    console.print(_HELP)
                else:
                    console.print(_HELP)
                continue
            transcript.user(line)
            console.print()
            await chat.send(line)
    finally:
        transcript.close()
        if resources:
            with console.status("[muted]destroying resources…[/]", spinner="dots"):
                for resource in resources:
                    await resource.stop()
        await backend.close()
        console.print(f"[muted]harness closed · transcript →[/] [accent]{transcript.path}[/]")
