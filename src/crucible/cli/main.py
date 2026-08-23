"""CRUCIBLE entry point: banner, then the main menu loop — all inline, one event loop."""

from __future__ import annotations

import asyncio
import subprocess
import sys

from crucible import __version__
from crucible.cli import (
    about_cmd,
    benchmark_cmd,
    config_cmd,
    harness_cmd,
    reports_cmd,
    session_cmd,
)
from crucible.cli.ui import console, print_banner, select
from crucible.config import load

MENU = [
    ("⚖  Benchmark — hardware & model speed", "benchmark"),
    ("⚗  Session — LLM in a sandbox shell", "session"),
    ("⌘  Harness — chat + spawn resources", "harness"),
    ("⌾  Reports — read past runs", "reports"),
    ("⚙  Config — backends, models, sandbox", "config"),
    ("✦  About — what is CRUCIBLE?", "about"),
    ("⏻  Quit", "quit"),
]

_COMMANDS = {
    "benchmark": benchmark_cmd.run,
    "session": session_cmd.run,
    "harness": harness_cmd.run,
    "reports": reports_cmd.run,
    "config": config_cmd.run,
    "about": about_cmd.run,
}


async def _menu_loop() -> None:
    cfg = load()
    while True:
        choice = await select("What are we testing today?", MENU)
        if choice in (None, "quit"):
            return
        await _COMMANDS[choice](cfg)
        console.print()


def _cleanup_leftovers() -> None:
    """Belt & braces: remove session containers THIS process left behind (crashes,
    Ctrl+C mid-session). Other crucible instances' sessions are never touched."""
    from crucible.sessions.docker import live_containers

    if not live_containers:
        return
    try:
        subprocess.run(["docker", "rm", "-f", *live_containers],
                       capture_output=True, timeout=30)
        console.print(f"[muted]cleaned up {len(live_containers)} leftover session container(s)[/]")
        live_containers.clear()
    except (OSError, subprocess.SubprocessError):
        pass


def main() -> None:
    if "--version" in sys.argv or "-V" in sys.argv:
        print(f"crucible {__version__}")
        return
    print_banner()
    try:
        asyncio.run(_menu_loop())
    except KeyboardInterrupt:
        console.print()
    finally:
        _cleanup_leftovers()
        console.print("[brand]forged & tested.[/] [muted]— CRUCIBLE[/]")


if __name__ == "__main__":
    main()
