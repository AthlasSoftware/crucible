"""Headless TUI smoke tests via Textual's pilot — catches compose/mount wiring bugs."""

import pytest

from crucible.app import CrucibleApp


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Keep the test hermetic: config/data under tmp, no live backend calls needed
    # for mounting (model loads fail gracefully into status text).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return CrucibleApp()


async def test_home_menu_and_navigation(app):
    from crucible.ui.about import AboutScreen
    from crucible.ui.benchmark_ui import BenchmarkScreen
    from crucible.ui.config_ui import ConfigScreen
    from crucible.ui.home import HomeScreen
    from crucible.ui.session_ui import SessionScreen

    async with app.run_test(size=(100, 40)) as pilot:
        assert isinstance(app.screen, HomeScreen)
        # navigate to each section and back
        for key, screen_cls in (("b", BenchmarkScreen), ("s", SessionScreen),
                                ("c", ConfigScreen), ("a", AboutScreen)):
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, screen_cls), f"{key} -> {screen_cls.__name__}"
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)


async def test_narrow_terminal_uses_small_banner(app):
    from textual.widgets import Static

    async with app.run_test(size=(50, 30)) as pilot:
        await pilot.pause()
        banner = app.screen.query_one("#banner", Static)
        # narrow terminals get the compact box-drawing banner, not the block art
        assert "╔═╗" in str(banner.content)
        assert "██████╗" not in str(banner.content)


async def test_quit_without_sessions_exits(app):
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("q")
        await pilot.pause()
    assert app.return_value is None  # exited cleanly


async def test_quit_with_live_session_prompts(app):
    from crucible.sessions import DockerSandbox
    from crucible.ui.widgets import ConfirmModal

    async with app.run_test(size=(100, 40)) as pilot:
        sandbox = DockerSandbox("ubuntu:24.04")
        sandbox.alive = True  # simulate a live container without docker
        app.register_sandbox(sandbox)
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        await pilot.press("n")  # stay
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal)
        sandbox.alive = False  # let teardown quit cleanly
        await pilot.press("ctrl+q")


async def test_config_screen_roundtrip(app, tmp_path):
    from textual.widgets import Input

    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.press("c")
        await pilot.pause()
        runs = app.screen.query_one("#cfg-runs", Input)
        assert runs.value == "3"
        runs.value = "5"
        await pilot.press("ctrl+s")
        await pilot.pause()
    from crucible.config import load
    assert load().benchmark.runs == 5
