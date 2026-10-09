import asyncio
from unittest.mock import AsyncMock

import pytest
from textual.widgets import Button, DataTable, Input, Select, SelectionList, Static
from textual.widgets.selection_list import Selection

from crucible import hardware
from crucible.app import CrucibleApp
from crucible.resources import ContainerInfo
from crucible.servers import VllmServer
from crucible.sessions import DockerSandbox
from crucible.ui import resources_ui
from crucible.ui.benchmark_ui import BenchmarkScreen
from crucible.ui.resources_ui import ResourcesScreen
from crucible.ui.widgets import ConfirmModal


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    hw = hardware.HardwareInfo("Test CPU", 4, 8, 3000, 0, 32, 4, 100, 80, "Test OS")
    monkeypatch.setattr(hardware, "probe", lambda: hw)
    monkeypatch.setattr(DockerSandbox, "count_sessions", AsyncMock(return_value=0))
    monkeypatch.setattr(VllmServer, "count", AsyncMock(return_value=0))
    monkeypatch.setattr(resources_ui, "list_containers", AsyncMock(return_value=[]))

    def models(screen):
        choices = screen.query_one("#bench-models", SelectionList)
        choices.clear_options()
        choices.add_option(Selection("example:8b", "example:8b", True))

    monkeypatch.setattr(BenchmarkScreen, "_load_models", models)
    monkeypatch.setattr(BenchmarkScreen, "_show_model_facts", lambda *args: None)
    return CrucibleApp()


async def test_container_controls_confirm_cancel_and_restore(app, monkeypatch):
    item = ContainerInfo("a" * 64, "other-service", "running", "example:latest")
    changed = []

    async def discover(**kwargs):
        return [item]

    async def control(cid, action, cfg):
        changed.append((cid, action))
        item.state = {"pause": "paused", "unpause": "running", "stop": "exited", "start": "running"}[action]

    monkeypatch.setattr(resources_ui, "list_containers", discover)
    monkeypatch.setattr(resources_ui, "control_container", control)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, ResourcesScreen)
        assert app.screen.query_one(DataTable).row_count == 1
        await pilot.click("#resource-pause")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmModal)
        assert app.focused.id == "confirm-no"
        await pilot.press("enter")  # default is Cancel
        await pilot.pause()
        assert changed == []
        for action in ("pause", "unpause", "stop", "start"):
            await pilot.click(f"#resource-{action}")
            await pilot.press("y")
            await pilot.pause()
            assert isinstance(app.screen, ResourcesScreen)
            assert changed[-1] == (item.id, action)
        await pilot.press("escape")
        assert item.state == "running"


async def test_resources_disables_inference_endpoint_controls(app, monkeypatch):
    item = ContainerInfo("a" * 64, "inference", "running", ports={"8000/tcp": [{"HostPort": "11434"}]})
    monkeypatch.setattr(resources_ui, "list_containers", AsyncMock(return_value=[item]))
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        assert app.screen.query_one("#resource-pause", Button).disabled
        assert app.screen.query_one("#resource-stop", Button).disabled
        assert "inference endpoint" in str(app.screen.query_one("#resource-details", Static).content)


async def test_slow_inventory_is_not_cancelled_by_another_refresh(app, monkeypatch):
    ready = asyncio.Event()
    entered = asyncio.Event()
    requests = 0

    async def discover(**kwargs):
        nonlocal requests
        requests += 1
        entered.set()
        await ready.wait()
        return [ContainerInfo("a" * 64, "other-service", "running")]

    monkeypatch.setattr(resources_ui, "list_containers", discover)
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("e")
        await entered.wait()
        app.screen.action_refresh()
        await pilot.pause()
        ready.set()
        await pilot.pause()
        assert requests == 1
        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.parametrize("size", [(80, 28), (130, 45)])
async def test_benchmark_settings_are_effective_and_accessible(app, size):
    async with app.run_test(size=size) as pilot:
        await pilot.press("b")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#bench-mode", Select).value = "std"
        await pilot.pause()
        screen.query_one("#bench-runs", Input).value = "7"
        screen.query_one("#bench-maxtok", Input).value = "64"
        await pilot.pause()
        assert screen._params().reps == 7
        assert screen._params().tg_tokens == 64
        assert "7 reps" in str(screen.query_one("#bench-start", Button).label)
        assert screen.query_one("#bench-resources", Button).region.bottom < size[1]
        screen.query_one("#bench-runs", Input).value = "0"
        await pilot.pause()
        assert screen.query_one("#bench-start", Button).disabled
        screen.action_resources()
        await pilot.pause()
        assert isinstance(app.screen, ResourcesScreen)


async def test_running_benchmark_cannot_be_hidden_behind_another_screen(app):
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.press("b")
        await pilot.pause()
        screen = app.screen
        screen._set_stage("bench-run")
        await pilot.press("escape")
        assert app.screen is screen
        await pilot.press("e")
        assert app.screen is screen


async def test_unavailable_docker_is_actionable_and_has_no_enabled_controls(app, monkeypatch):
    from crucible.sessions.docker import SandboxError

    monkeypatch.setattr(resources_ui, "list_containers", AsyncMock(side_effect=SandboxError("Docker unavailable: permission denied")))
    async with app.run_test(size=(80, 28)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        assert "permission denied" in str(app.screen.query_one("#resource-status", Static).content)
        for action in ("pause", "unpause", "stop", "start"):
            assert app.screen.query_one(f"#resource-{action}", Button).disabled


async def test_results_open_at_interpretation_instead_of_bottom_actions(app):
    from crucible.backends.base import GenResult
    from crucible.benchmark.engine import run_row, summarize

    rows = [run_row("p", GenResult("example", .2, 100, 31, 1, 1.2,
                                  prompt_eval_s=.1, chunk_offsets_s=[.2, .3, 1.2]))]
    report = {"schema": 3, "backend": "ollama", "status": "complete",
              "params": {"warmup": True},
              "results": [{"model": f"example-{n}", "runs": rows, "summary": summarize(rows)}
                          for n in range(3)]}
    async with app.run_test(size=(80, 28)) as pilot:
        await pilot.press("b")
        await pilot.pause()
        screen = app.screen
        screen._show_results(report)
        await pilot.pause()
        view = screen.query_one("#bench-results")
        assert app.focused is view
        assert view.scroll_y == 0
        assert "Wait for first text" in str(screen.query_one("#results-overview", Static).content)
        view.scroll_end(animate=False)
        await pilot.pause()
        assert view.scroll_y > 0
        screen._show_results(report)
        await pilot.pause()
        assert view.scroll_y == 0
