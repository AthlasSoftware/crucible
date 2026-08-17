"""Config flow: show the config, quick-set the common fields, or open $EDITOR."""

from __future__ import annotations

import os
import subprocess

from rich.table import Table

from crucible.backends import get_backend
from crucible.cli.common import fetch_models
from crucible.cli.ui import console, rule, select
from crucible.config import APPROVAL_MODES, BACKEND_NAMES, Config, config_path, load


def _table(cfg: Config) -> Table:
    table = Table(title=str(config_path()), title_style="muted", border_style="grey30",
                  show_header=False, pad_edge=False)
    table.add_column(style="accent", no_wrap=True)
    table.add_column()
    endpoint = cfg.backend.endpoint()
    table.add_row("backend", f"[brand]{cfg.backend.active}[/] @ {endpoint.base_url}")
    table.add_row("default model", endpoint.default_model or "[muted]first available[/]")
    table.add_row("benchmark", f"runs={cfg.benchmark.runs} · max_tokens={cfg.benchmark.max_tokens}"
                               f" · warmup={'on' if cfg.benchmark.warmup else 'off'}")
    table.add_row("sandbox", f"{cfg.sandbox.image} · {cfg.sandbox.cpus} cpus · "
                             f"{cfg.sandbox.memory} · network "
                             f"{'on' if cfg.sandbox.network else 'off'}")
    table.add_row("sessions", f"approval={cfg.sandbox.approval} · "
                              f"timeout={cfg.sandbox.command_timeout_s}s · "
                              f"max_iterations={cfg.sandbox.max_iterations}")
    return table


async def run(cfg: Config) -> None:
    rule("config")
    while True:
        console.print(_table(cfg))
        action = await select(
            "Change?",
            [
                ("active backend", "backend"),
                ("default model (pick from live list)", "model"),
                ("approval mode", "approval"),
                ("sandbox network on/off", "network"),
                ("what do the settings mean?", "help"),
                ("open in $EDITOR", "editor"),
                ("← back", "back"),
            ],
            default="back",
        )
        if action in (None, "back"):
            return
        if action == "help":
            _print_help()
            continue
        if action == "backend":
            choice = await select("Active backend?", [(n, n) for n in BACKEND_NAMES],
                                  default=cfg.backend.active)
            if choice:
                cfg.backend.active = choice
        elif action == "model":
            await _pick_default_model(cfg)
        elif action == "approval":
            choice = await select("Approval mode?", [(m, m) for m in APPROVAL_MODES],
                                  default=cfg.sandbox.approval)
            if choice:
                cfg.sandbox.approval = choice
        elif action == "network":
            cfg.sandbox.network = not cfg.sandbox.network
        elif action == "editor":
            editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
            cfg.save()
            subprocess.run([editor, str(config_path())], check=False)
            cfg_new = load()
            cfg.backend, cfg.benchmark, cfg.sandbox = (
                cfg_new.backend, cfg_new.benchmark, cfg_new.sandbox)
            console.print("[ok]config reloaded[/]")
            continue
        cfg.save()
        console.print("[ok]saved[/]")


def _print_help() -> None:
    console.print("""
[bold]Backend[/] [muted]— used by benchmarks ⚖ and sessions ⚗[/]
  [accent]active[/]          which LLM server benchmarks and sessions talk to
  [accent]base_url[/]        where that server listens (per backend)
  [accent]default_model[/]   preselected model in pickers
  [accent]api_key[/]         bearer token for openai-compatible endpoints that need one

[bold]Benchmark ⚖[/] [muted]— only affects benchmark runs[/]
  [accent]runs[/]            times each of the 4 prompts is generated per model —
                  more runs = steadier averages, longer benchmark
  [accent]max_tokens[/]      cap on generated tokens per run — bigger = steadier gen-t/s
  [accent]warmup[/]          one untimed generation first, so cold model loading
                  doesn't skew run 1

[bold]Sandbox ⚗[/] [muted]— only affects sessions[/]
  [accent]image[/]           docker image the model gets its shell in
  [accent]cpus / memory[/]   resource limits for the container
  [accent]network[/]         internet access for the sandbox (needed for apt/pip) —
                  off = fully isolated
  [accent]approval[/]        approve-each: you confirm every command · auto: runs freely
  [accent]command_timeout_s[/]  seconds before a sandbox command is killed
  [accent]max_iterations[/]  command steps per task before the agent loop stops
""")


async def _pick_default_model(cfg: Config) -> None:
    backend = get_backend(cfg)
    try:
        models = await fetch_models(backend)
        if not models:
            return
        choice = await select("Default model?", [(m.id, m.id) for m in models],
                              default=cfg.backend.endpoint().default_model or models[0].id)
        if choice:
            cfg.backend.endpoint().default_model = choice
    finally:
        await backend.close()
