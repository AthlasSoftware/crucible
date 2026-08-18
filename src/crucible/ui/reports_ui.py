"""Reports screen: browse benchmark reports & session transcripts, rendered in-app."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Markdown, OptionList, Rule, Static
from textual.widgets.option_list import Option

from crucible.report import ReportEntry, list_reports
from crucible.ui.widgets import ScreenHeader

_ICONS = {"benchmark": "⚖", "session": "⚗"}

_EMPTY_MD = (
    "*Nothing here yet.*\n\n"
    "Every benchmark and session is saved here automatically.\n\n"
    "**b** — run a benchmark · **s** — start a session"
)


def _label(entry: ReportEntry) -> str:
    when = datetime.fromtimestamp(entry.mtime).strftime("%m-%d %H:%M")
    return f"{_ICONS.get(entry.kind, '·')}  {when}  {entry.title}"


class ReportsScreen(Screen):
    _entries: list = []  # check_action can run before on_mount

    BINDINGS = [
        ("escape", "back", "Back"),
        ("enter", "read", "Read", ),
        ("e", "open_editor", "Open in $EDITOR"),
        ("r", "refresh", "Refresh"),
        ("b", "goto('benchmark')", "Benchmark", ),
        ("s", "goto('sessions')", "Session"),
    ]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE ▸ Reports")
        yield Rule()
        with Horizontal(id="reports-body", classes="hero"):
            with Vertical(id="reports-list"):
                yield Static("RUNS & TRANSCRIPTS", classes="micro first")
                yield OptionList(id="reports-options")
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

    def check_action(self, action: str, parameters) -> bool | None:
        if action in ("goto",):
            return not self._entries or None  # only advertise b/s when list is empty
        return True

    def action_refresh(self) -> None:
        option_list = self.query_one("#reports-options", OptionList)
        highlighted = option_list.highlighted
        option_list.clear_options()
        self._entries = list_reports()
        self.query_one(ScreenHeader).set_context(f"{len(self._entries)} files on disk")
        if not self._entries:
            self.query_one("#report-view", Markdown).update(_EMPTY_MD)
            self.refresh_bindings()
            return
        option_list.add_options(
            [Option(_label(e), id=str(i)) for i, e in enumerate(self._entries)]
        )
        option_list.highlighted = highlighted if highlighted is not None else 0
        option_list.focus()
        self.refresh_bindings()

    def action_goto(self, target: str) -> None:
        self.app.push_screen(target)

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

    def action_read(self) -> None:
        self.query_one("#report-view-scroll", VerticalScroll).focus()

    def action_back(self) -> None:
        # esc from the reader returns to the list; esc from the list leaves.
        viewer = self.query_one("#report-view-scroll", VerticalScroll)
        if viewer.has_focus:
            self.query_one("#reports-options", OptionList).focus()
        else:
            self.app.pop_screen()

    def action_open_editor(self) -> None:
        option_list = self.query_one("#reports-options", OptionList)
        if option_list.highlighted is None or not self._entries:
            return
        entry = self._entries[option_list.highlighted]
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
        with self.app.suspend():
            subprocess.run([editor, str(entry.path)], check=False)
