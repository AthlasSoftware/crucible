"""Shared console, styling, and prompt helpers for the inline CLI.

Interactive terminals get refined arrow-key prompts (questionary); non-TTY
usage (pipes, CI) falls back to plain numbered prompts, which doubles as a
scriptable interface — empty input always means "accept the default".
"""

from __future__ import annotations

import sys

import questionary
from questionary import Choice
from questionary import Style as QStyle
from rich.console import Console
from rich.theme import Theme

from crucible.banner import (
    BANNER,
    BANNER_SMALL,
    BANNER_SMALL_WIDTH,
    BANNER_WIDTH,
    SUBTITLE,
    TAGLINE,
)

EMBER = "#ff8700"

console = Console(
    highlight=False,
    theme=Theme(
        {
            "ember": f"bold {EMBER}",
            "accent": "cyan",
            "muted": "grey58",
            "ok": "green",
            "err": "bold red",
            "cmd": "bold yellow",
        }
    ),
)

QSTYLE = QStyle(
    [
        ("qmark", f"fg:{EMBER} bold"),
        ("question", "bold"),
        ("answer", f"fg:{EMBER}"),
        ("pointer", f"fg:{EMBER} bold"),
        ("highlighted", f"fg:{EMBER}"),
        ("selected", "fg:#ffaf00"),
        ("instruction", "fg:#666666"),
    ]
)
QMARK = "◆"


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def print_banner() -> None:
    small = console.width <= BANNER_WIDTH + 2
    art = BANNER_SMALL if small else BANNER
    width = BANNER_SMALL_WIDTH if small else BANNER_WIDTH
    console.print()
    console.print(art, style="ember")
    console.print(SUBTITLE.rjust(width), style="muted")
    console.print(TAGLINE.center(width).rstrip(), style="italic")
    console.print()


def rule(title: str = "") -> None:
    console.print()
    console.rule(f"[ember]{title}[/]" if title else "", style="grey30", align="left")


def select(message: str, choices: list[tuple[str, str]], default: str | None = None) -> str | None:
    """Pick one value; returns None on cancel/EOF."""
    if interactive():
        q_choices = [Choice(title=label, value=value) for label, value in choices]
        default_choice = next((c for c in q_choices if c.value == default), None)
        return questionary.select(
            message, choices=q_choices, default=default_choice, style=QSTYLE, qmark=QMARK
        ).ask()
    console.print(f"[accent]{message}[/]")
    for i, (label, value) in enumerate(choices, 1):
        marker = " [muted](default)[/]" if value == default else ""
        console.print(f"  {i}. {label}{marker}")
    try:
        raw = input("> ").strip()
    except EOFError:
        return None
    if not raw:
        return default if default is not None else choices[0][1]
    try:
        return choices[int(raw) - 1][1]
    except (ValueError, IndexError):
        return None


def checkbox(
    message: str, choices: list[tuple[str, str]], preselected: set[str] | None = None
) -> list[str]:
    preselected = preselected or set()
    if interactive():
        q_choices = [
            Choice(title=label, value=value, checked=value in preselected)
            for label, value in choices
        ]
        result = questionary.checkbox(message, choices=q_choices, style=QSTYLE, qmark=QMARK).ask()
        return result or []
    console.print(f"[accent]{message}[/] [muted](comma-separated numbers, empty = default)[/]")
    for i, (label, value) in enumerate(choices, 1):
        marker = " [muted]✓[/]" if value in preselected else ""
        console.print(f"  {i}. {label}{marker}")
    try:
        raw = input("> ").strip()
    except EOFError:
        return []
    if not raw:
        return [v for _, v in choices if v in preselected]
    picked = []
    for part in raw.split(","):
        try:
            picked.append(choices[int(part.strip()) - 1][1])
        except (ValueError, IndexError):
            continue
    return picked


def text(message: str, default: str = "") -> str:
    if interactive():
        answer = questionary.text(message, default=default, style=QSTYLE, qmark=QMARK).ask()
        return answer if answer is not None else default
    try:
        raw = input(f"{message} [{default}] > ").strip()
    except EOFError:
        return default
    return raw or default


def confirm(message: str, default: bool = True) -> bool:
    if interactive():
        answer = questionary.confirm(message, default=default, style=QSTYLE, qmark=QMARK).ask()
        return bool(answer) if answer is not None else False
    try:
        raw = input(f"{message} [{'Y/n' if default else 'y/N'}] > ").strip().lower()
    except EOFError:
        return default
    if not raw:
        return default
    return raw in ("y", "yes", "j", "ja")
