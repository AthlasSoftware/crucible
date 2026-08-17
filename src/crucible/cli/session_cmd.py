"""Session flow: an LLM with a sandboxed shell, inline in your terminal."""

from __future__ import annotations

import asyncio

from rich.markup import escape

from crucible.cli.common import pick_backend, pick_models
from crucible.cli.ui import confirm, console, interactive, rule, select, text
from crucible.config import Config
from crucible.report import SessionTranscript
from crucible.sessions import AgentCallbacks, AgentLoop, DockerSandbox
from crucible.sessions.docker import SandboxError


def run(cfg: Config) -> None:
    rule("session")
    asyncio.run(_run(cfg))


async def _run(cfg: Config) -> None:
    backend_name = pick_backend(cfg)
    if backend_name is None:
        return
    backend, models = await pick_models(cfg, backend_name, multi=False)
    if not models:
        await backend.close()
        return
    model = models[0]

    sandbox = DockerSandbox(
        image=cfg.sandbox.image, cpus=cfg.sandbox.cpus,
        memory=cfg.sandbox.memory, network=cfg.sandbox.network,
    )
    console.print(
        f"[muted]sandbox: {cfg.sandbox.image} · {cfg.sandbox.cpus} cpus · "
        f"{cfg.sandbox.memory} · network {'on' if cfg.sandbox.network else 'off'} · "
        f"approval {cfg.sandbox.approval}[/]"
    )
    try:
        with console.status("[muted]starting sandbox…[/]", spinner="dots"):
            await sandbox.create(on_output=lambda _s, line: None)
    except SandboxError as e:
        console.print(f"[err]sandbox failed: {e}[/]")
        await backend.close()
        return
    console.print(f"[ok]⚗ session {sandbox.id} up[/] [muted]— {model} @ {backend_name}[/]")

    transcript = SessionTranscript.create(
        sandbox.id, model, backend_name, cfg.sandbox.image, cfg.sandbox.network
    )
    console.print(f"[muted]transcript →[/] [accent]{transcript.path}[/]")

    streaming = False

    def end_stream() -> None:
        nonlocal streaming
        if streaming:
            console.print()  # newline after streamed tokens
            streaming = False

    def on_token(token: str) -> None:
        nonlocal streaming
        streaming = True
        console.print(escape(token), end="", style="grey74")

    async def request_approval(command: str) -> str:
        end_stream()
        answer = select(
            "Run this command?",
            [("run", "yes"), ("deny", "no"), ("auto — run everything from now on", "auto")],
            default="yes",
        )
        return answer or "no"

    def on_output(stream: str, line: str) -> None:
        end_stream()
        transcript.output(stream, line)
        style = {"stderr": "red", "info": "muted"}.get(stream, "")
        prefix = "[grey30]│[/] "
        console.print(prefix + (f"[{style}]{escape(line)}[/]" if style else escape(line)))

    def assistant_done(text_: str) -> None:
        end_stream()
        transcript.assistant(text_.strip())

    callbacks = AgentCallbacks(
        on_chat_token=on_token,
        on_assistant_done=assistant_done,
        on_command=lambda c: (end_stream(), transcript.command(c),
                              console.print(f"[cmd]$ {escape(c)}[/]"))[-1],
        on_output=on_output,
        on_mode_change=lambda m: (transcript.note(f"protocol switched to {m} mode"),
                                  console.print(f"[muted]· protocol: {m} mode[/]"))[-1],
        on_done=lambda s: (end_stream(), transcript.done(s),
                           console.print(f"[ok]✓ DONE:[/] {escape(s)}"))[-1],
        on_info=lambda s: (end_stream(), transcript.note(s),
                           console.print(f"[muted]· {escape(s)}[/]"))[-1],
        on_error=lambda s: (end_stream(), transcript.note(f"error: {s}"),
                            console.print(f"[err]✗ {escape(s)}[/]"))[-1],
        request_approval=request_approval,
    )
    agent = AgentLoop(backend, sandbox, model, cfg.sandbox, callbacks)

    try:
        while True:
            console.print()
            task = text("task ▸") if interactive() else text("task ▸", "")
            if not task.strip():
                if confirm("End the session?"):
                    break
                continue
            transcript.user(task.strip())
            console.print()
            try:
                await agent.run_task(task.strip())
            except KeyboardInterrupt:
                agent.request_stop()
                end_stream()
                console.print("\n[muted]· stopped[/]")
    finally:
        transcript.close()
        with console.status("[muted]destroying sandbox…[/]", spinner="dots"):
            await sandbox.destroy()
        await backend.close()
        console.print(f"[muted]session {sandbox.id} destroyed · "
                      f"transcript →[/] [accent]{transcript.path}[/]")
