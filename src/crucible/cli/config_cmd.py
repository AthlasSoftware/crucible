"""Config flow: show the config, quick-set the common fields, or open $EDITOR."""

from __future__ import annotations

import asyncio
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
    table.add_row("backend", f"[ember]{cfg.backend.active}[/] @ {endpoint.base_url}")
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


def run(cfg: Config) -> None:
    rule("config")
    while True:
        console.print(_table(cfg))
        action = select(
            "Change?",
            [
                ("active backend", "backend"),
                ("default model (pick from live list)", "model"),
                ("approval mode", "approval"),
                ("sandbox network on/off", "network"),
                ("open in $EDITOR", "editor"),
                ("← back", "back"),
            ],
            default="back",
        )
        if action in (None, "back"):
            return
        if action == "backend":
            choice = select("Active backend?", [(n, n) for n in BACKEND_NAMES],
                            default=cfg.backend.active)
            if choice:
                cfg.backend.active = choice
        elif action == "model":
            asyncio.run(_pick_default_model(cfg))
        elif action == "approval":
            choice = select("Approval mode?", [(m, m) for m in APPROVAL_MODES],
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


async def _pick_default_model(cfg: Config) -> None:
    backend = get_backend(cfg)
    try:
        models = await fetch_models(backend)
        if not models:
            return
        choice = select("Default model?", [(m.id, m.id) for m in models],
                        default=cfg.backend.endpoint().default_model or models[0].id)
        if choice:
            cfg.backend.endpoint().default_model = choice
    finally:
        await backend.close()
