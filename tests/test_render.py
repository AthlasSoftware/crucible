"""Rendering model text: Markdown when asked, raw when asked, never markup."""

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from crucible import render
from crucible.config import Config


def _to_text(renderable, width=70) -> str:
    console = Console(width=width, file=open("/dev/null", "w"), record=True)
    console.print(renderable)
    return console.export_text()


def test_markdown_is_the_default():
    assert Config().ui.render_markdown is True


def test_markdown_mode_returns_markdown():
    assert isinstance(render.model_text("# hi", markdown=True), Markdown)


def test_raw_mode_returns_plain_text():
    assert isinstance(render.model_text("# hi", markdown=False), Text)


def test_raw_mode_preserves_the_source_exactly():
    source = "# heading\n\n- a\n- b\n\n`code`"
    assert render.model_text(source, markdown=False).plain == source


def test_headings_and_lists_are_rendered():
    out = _to_text(render.model_text("# Title\n\n- one\n- two", markdown=True))
    assert "Title" in out
    assert "#" not in out  # the marker is consumed, not printed
    assert "one" in out and "two" in out


def test_fenced_code_survives_rendering():
    out = _to_text(render.model_text("Try:\n\n```sh\necho hi\n```", markdown=True))
    assert "echo hi" in out
    assert "```" not in out


def test_console_markup_in_model_output_is_never_interpreted():
    """A model writing [bold] must not be able to style CRUCIBLE's own output."""
    hostile = "[bold red]not a style[/] [blink]nope[/]"
    for markdown in (True, False):
        out = _to_text(render.model_text(hostile, markdown=markdown))
        assert "not a style" in out
        assert "[bold red]" in out or "bold red" in out


def test_looks_like_markdown_spots_structure():
    assert render.looks_like_markdown("- a list")
    assert render.looks_like_markdown("some `code`")
    assert render.looks_like_markdown("line one\nline two")
    assert render.looks_like_markdown("**bold**")
    assert render.looks_like_markdown("| a | b |")


def test_looks_like_markdown_leaves_one_liners_alone():
    """A short plain answer should stay on the speaker's line."""
    assert not render.looks_like_markdown("Stockholm")
    assert not render.looks_like_markdown("The answer is 42")


def test_hyphen_in_prose_is_not_a_list():
    assert not render.looks_like_markdown("a well-known result")


# ---- the chat views honour the setting -------------------------------------

_REPLY = "## Fruit\n\n- Apple\n- Banana\n\n**ripe**"


async def _harness_log_after_reply(tmp_path, monkeypatch, markdown: bool) -> str:
    from textual.widgets import RichLog

    from crucible.app import CrucibleApp

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    app = CrucibleApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.config.ui.render_markdown = markdown
        await pilot.press("h")
        await pilot.pause()
        # The chat log lives in the hidden half of the ContentSwitcher during
        # setup, so it has no width and renders nothing until the run stage.
        app.screen._set_stage("harn-run")
        await pilot.pause()
        app.screen._write_reply(_REPLY)
        await pilot.pause()
        log = app.screen.query_one("#harn-chat", RichLog)
        return "\n".join(strip.text for strip in log.lines)


async def test_harness_renders_markdown_when_enabled(tmp_path, monkeypatch):
    out = await _harness_log_after_reply(tmp_path, monkeypatch, markdown=True)
    assert "Fruit" in out
    assert "##" not in out  # markers consumed
    assert "- Apple" not in out  # turned into a bullet
    assert "**" not in out


async def test_harness_shows_raw_text_when_disabled(tmp_path, monkeypatch):
    """Raw mode must show exactly what the model emitted, markers and all."""
    out = await _harness_log_after_reply(tmp_path, monkeypatch, markdown=False)
    assert "## Fruit" in out
    assert "- Apple" in out
    assert "**ripe**" in out
