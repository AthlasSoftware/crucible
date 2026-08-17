"""Reports screen: browse benchmark reports & session transcripts, rendered in-app."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from crucible.report import ReportEntry, list_reports

_ICONS = {"benchmark": "⚖", "session": "⚗"}


def _label(entry: ReportEntry) -> str:
    when = datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M")
    return f"{_ICONS.get(entry.kind, '·')}  {when}  {entry.kind:<9}  {entry.title}"


class ReportsScreen(Screen):
    BINDINGS = [
        ("escape", "app.pop_screen", "Back"),
        ("r", "refresh", "Refresh"),
        ("e", "open_editor", "Open in $EDITOR"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("CRUCIBLE — Reports", classes="screen-title")
        with Horizontal(id="reports-body"):
            yield OptionList(id="reports-list")
            with VerticalScroll(id="report-view-scroll"):
                yield Markdown("*select a report*", id="report-view")
        yield Footer()

    def on_mount(self) -> None:
        if getattr(self, "_ready", False):
            return
        self._ready = True
        self._entries: list[ReportEntry] = []
        self.action_refresh()

    def on_screen_resume(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        option_list = self.query_one("#reports-list", OptionList)
        highlighted = option_list.highlighted
        option_list.clear_options()
        self._entries = list_reports()
        if not self._entries:
            self.query_one("#report-view", Markdown).update(
                "*no reports yet — run a benchmark or a session first*"
            )
            return
        option_list.add_options(
            [Option(_label(e), id=str(i)) for i, e in enumerate(self._entries)]
        )
        option_list.highlighted = highlighted if highlighted is not None else 0
        option_list.focus()

    def _entry_at(self, option_id: str | None) -> ReportEntry | None:
        try:
            return self._entries[int(option_id or "")]
        except (ValueError, IndexError):
            return None

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        entry = self._entry_at(event.option.id)
        if entry is None:
            return
        try:
            content = entry.path.read_text(encoding="utf-8")
        except OSError as e:
            content = f"*cannot read {entry.path}: {e}*"
        self.query_one("#report-view", Markdown).update(content)

    def action_open_editor(self) -> None:
        option_list = self.query_one("#reports-list", OptionList)
        if option_list.highlighted is None:
            return
        entry = self._entries[option_list.highlighted]
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        with self.app.suspend():
            subprocess.run([editor, str(entry.path)], check=False)
