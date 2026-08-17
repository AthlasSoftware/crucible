"""Reports flow: browse and read benchmark reports & session transcripts inline."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime

from rich.markdown import Markdown

from crucible.cli.ui import console, rule, select
from crucible.config import Config
from crucible.report import ReportEntry, list_reports

_ICONS = {"benchmark": "⚖", "session": "⚗"}


def _label(entry: ReportEntry) -> str:
    when = datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M")
    return f"{_ICONS.get(entry.kind, '·')}  {when}  {entry.kind:<9}  {entry.title}"


async def run(cfg: Config) -> None:
    rule("reports")
    while True:
        entries = list_reports()
        if not entries:
            console.print("[muted]no reports yet — run a benchmark or a session first[/]")
            return
        choices = [(_label(e), str(i)) for i, e in enumerate(entries)]
        choices.append(("← back", "back"))
        picked = await select(f"{len(entries)} report(s) — read which?", choices)
        if picked in (None, "back"):
            return
        entry = entries[int(picked)]
        console.print()
        console.print(Markdown(entry.path.read_text(encoding="utf-8")))
        console.print(f"\n[muted]file →[/] [accent]{entry.path}[/]")
        action = await select(
            "Next?",
            [("another report", "again"), ("open in $EDITOR", "editor"), ("← back", "back")],
            default="again",
        )
        if action == "editor":
            editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
            subprocess.run([editor, str(entry.path)], check=False)
        elif action != "again":
            return
