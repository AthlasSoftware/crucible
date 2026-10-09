"""Headless TUI smoke tests via Textual's pilot — catches compose/mount wiring bugs."""

from unittest.mock import AsyncMock

import pytest

from crucible.app import CrucibleApp


@pytest.fixture
def app(tmp_path, monkeypatch):
    from crucible.servers import VllmServer
    from crucible.sessions import DockerSandbox

    # Keep the test hermetic: config/data under tmp, no live backend calls needed
    # for mounting (model loads fail gracefully into status text).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    # A test must never discover or purge real labelled containers on the host.
    monkeypatch.setattr(VllmServer, "count", AsyncMock(return_value=0))
    monkeypatch.setattr(VllmServer, "purge", AsyncMock(return_value=0))
    monkeypatch.setattr(DockerSandbox, "count_sessions", AsyncMock(return_value=0))
    return CrucibleApp()


async def test_home_menu_and_navigation(app):
    from crucible.ui.about import AboutScreen
    from crucible.ui.benchmark_ui import BenchmarkScreen
    from crucible.ui.config_ui import ConfigScreen
    from crucible.ui.harness_ui import HarnessScreen
    from crucible.ui.home import HomeScreen
    from crucible.ui.session_ui import SessionScreen

    async with app.run_test(size=(100, 40)) as pilot:
        assert isinstance(app.screen, HomeScreen)
        # navigate to each section and back
        for key, screen_cls in (("b", BenchmarkScreen), ("s", SessionScreen),
                                ("h", HarnessScreen), ("c", ConfigScreen),
                                ("a", AboutScreen)):
            await pilot.press(key)
            await pilot.pause()
            assert isinstance(app.screen, screen_cls), f"{key} -> {screen_cls.__name__}"
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, HomeScreen)


async def test_harness_opens_with_no_containers(app):
    from textual.widgets import Static

    from crucible.ui.harness_ui import HarnessScreen

    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.press("h")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, HarnessScreen)
        # Resources are attached later, from the live view — opening a harness
        # must not spawn anything on its own.
        assert screen._stage == "harn-setup"
        assert screen.resources == []
        assert app.live_sandboxes == []
        assert screen.query_one("#harn-resources", Static) is not None


async def test_release_option_hidden_when_crucible_holds_nothing(app, monkeypatch):
    from crucible.sessions import DockerSandbox
    from crucible.ui.home import RELEASE_ID

    async def none(label=None):
        return 0

    monkeypatch.setattr(DockerSandbox, "count_sessions", none)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        menu = app.screen.query_one("#menu")
        assert all(o.id != RELEASE_ID for o in menu._options)


async def test_release_option_appears_for_session_and_harness_containers(app, monkeypatch):
    from crucible.sessions import DockerSandbox
    from crucible.sessions.docker import HARNESS_LABEL
    from crucible.ui.home import RELEASE_ID

    async def counted(label=None):
        # one stuck session plus two harness resources
        return 2 if label == HARNESS_LABEL else 1

    monkeypatch.setattr(DockerSandbox, "count_sessions", counted)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        menu = app.screen.query_one("#menu")
        first = menu.get_option_at_index(0)
        assert first.id == RELEASE_ID  # offered first, where it can't be missed
        assert "3 container(s)" in str(first.prompt)


async def test_release_purges_both_labels_and_nothing_else(app, monkeypatch):
    from crucible.sessions import DockerSandbox
    from crucible.sessions.docker import HARNESS_LABEL, LABEL
    from crucible.ui.widgets import ConfirmModal

    purged = []

    async def counted(label=None):
        return 1

    async def fake_purge(exclude=None, label=LABEL):
        purged.append(label)
        return 1

    monkeypatch.setattr(DockerSandbox, "count_sessions", counted)
    monkeypatch.setattr(DockerSandbox, "purge_stale", fake_purge)

    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        menu = app.screen.query_one("#menu")
        # The option appearing must not drag the highlight onto it — landing on
        # a destructive entry you did not navigate to is how accidents happen.
        assert menu.get_option_at_index(menu.highlighted).id == "benchmark"

        await pilot.press("up")  # deliberately move onto the killswitch
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)  # never fires without asking
        await pilot.press("y")
        await pilot.pause()

    assert sorted(purged) == sorted([LABEL, HARNESS_LABEL])


async def test_harness_config_roundtrip(app):
    from textual.widgets import Input

    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.press("c")
        await pilot.pause()
        tools = app.screen.query_one("#cfg-harn-tools", Input)
        assert tools.value == "12"
        tools.value = "4"
        await pilot.press("ctrl+s")
        await pilot.pause()
    from crucible.config import load
    assert load().harness.max_tool_calls == 4


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
