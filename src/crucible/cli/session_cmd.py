"""Session flow: an LLM with a sandboxed shell, inline in your terminal."""

from __future__ import annotations

from rich.markup import escape

from crucible import render
from crucible.cli.common import pick_backend, pick_models
from crucible.cli.ui import confirm, console, rule, select, text
from crucible.config import Config
from crucible.report import SessionTranscript
from crucible.sessions import AgentCallbacks, AgentLoop, DockerSandbox, protocol
from crucible.sessions.docker import SandboxError


async def run(cfg: Config) -> None:
    rule("session")
    backend_name = await pick_backend(cfg)
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

    # Streaming raw tokens and printing a rendered message would show the reply
    # twice, so the two are alternatives: render Markdown at the end of the turn,
    # or watch the tokens arrive.
    render_md = cfg.ui.render_markdown
    streaming = False

    def end_stream() -> None:
        nonlocal streaming
        if streaming:
            console.print()  # newline after streamed tokens
            streaming = False

    def on_token(token: str) -> None:
        nonlocal streaming
        if render_md:
            return
        streaming = True
        console.print(escape(token), end="", style="grey74")

    async def request_approval(command: str) -> str:
        end_stream()
        answer = await select(
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
        visible = protocol.strip_think(text_).strip()
        transcript.assistant(visible or text_.strip())
        if render_md and visible:
            console.print("[brand]model ▸[/]")
            console.print(render.model_text(visible, markdown=True))

    def on_command(command: str) -> None:
        end_stream()
        transcript.command(command)
        console.print(f"[cmd]$ {escape(command)}[/]")

    def on_mode_change(mode: str) -> None:
        transcript.note(f"protocol switched to {mode} mode")
        console.print(f"[muted]· protocol: {mode} mode[/]")

    def on_done(summary: str) -> None:
        end_stream()
        transcript.done(summary)
        console.print(f"[ok]✓ DONE:[/] {escape(summary)}")

    def on_info(info: str) -> None:
        end_stream()
        transcript.note(info)
        console.print(f"[muted]· {escape(info)}[/]")

    def on_error(error: str) -> None:
        end_stream()
        transcript.note(f"error: {error}")
        console.print(f"[err]✗ {escape(error)}[/]")

    callbacks = AgentCallbacks(
        on_chat_token=on_token,
        on_assistant_done=assistant_done,
        on_command=on_command,
        on_output=on_output,
        on_mode_change=on_mode_change,
        on_done=on_done,
        on_info=on_info,
        on_error=on_error,
        request_approval=request_approval,
    )
    agent = AgentLoop(backend, sandbox, model, cfg.sandbox, callbacks)

    try:
        while True:
            console.print()
            task = await text("task ▸")
            if not task.strip():
                if await confirm("End the session?"):
                    break
                continue
            transcript.user(task.strip())
            console.print()
            await agent.run_task(task.strip())
    finally:
        transcript.close()
        with console.status("[muted]destroying sandbox…[/]", spinner="dots"):
            await sandbox.destroy()
        await backend.close()
        console.print(f"[muted]session {sandbox.id} destroyed · "
                      f"transcript →[/] [accent]{transcript.path}[/]")
