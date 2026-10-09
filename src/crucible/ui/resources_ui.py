"""Inspect contention and explicitly control one selected container at a time."""

from __future__ import annotations

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Static

from crucible import hardware
from crucible.resources import (
    ContainerInfo,
    action_reason,
    control_container,
    list_containers,
    protection_reason,
)
from crucible.sessions.docker import SandboxError
from crucible.ui.widgets import ConfirmModal, MachineMonitor, ScreenHeader


class ResourcesScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back"), ("r", "refresh", "Refresh")]

    def compose(self) -> ComposeResult:
        yield ScreenHeader("CRUCIBLE ▸ Resources", "prepare the benchmark host")
        with Vertical(classes="hero"):
            yield Static("Pause suspends CPU work and retains memory. Stop terminates processes to release RAM/VRAM; start reloads them.",
                         classes="step-hint", markup=False)
            yield Static("Select one container. Confirm each change. Identified published inference endpoints and active Crucible resources are protected. Docker may use a remote daemon; GPU readings below are local.",
                         classes="step-hint", markup=False)
            yield DataTable(id="resource-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="resource-details", markup=False)
            yield Static("", id="resource-status", markup=False)
            with Horizontal(id="resource-actions"):
                yield Button("Pause", id="resource-pause", disabled=True)
                yield Button("Resume", id="resource-unpause", disabled=True)
                yield Button("Stop", id="resource-stop", variant="warning", disabled=True)
                yield Button("Start", id="resource-start", disabled=True)
                yield Button("Refresh", id="resource-refresh")
        yield MachineMonitor(id="resource-hw", compact=True)
        yield Footer()

    def on_mount(self) -> None:
        self._containers: dict[str, ContainerInfo] = {}
        self._selected: str | None = None
        self._busy = False
        self._prompting = False
        self._refreshing = False
        self.query_one(DataTable).add_columns("Container", "State", "Owner", "CPU", "RAM", "GPU VRAM¹", "Control")
        self.action_refresh()
        self.set_interval(5, self.action_refresh)

    def on_screen_resume(self) -> None:
        if hasattr(self, "_busy"):
            self.action_refresh()

    @work(group="resources-refresh")
    async def action_refresh(self) -> None:
        if self._refreshing or self._busy or self._prompting or self.app.screen is not self:
            return
        # Slow Docker/GPU probes must finish; a periodic refresh should not
        # repeatedly cancel them before any inventory can reach the screen.
        self._refreshing = True
        try:
            await self._refresh_contents()
        finally:
            self._refreshing = False

    async def _refresh_contents(self) -> None:
        status = self.query_one("#resource-status", Static)
        try:
            containers = await list_containers(stats=True)
            hw = await asyncio.to_thread(hardware.probe)
        except SandboxError as e:
            self._containers = {}
            self.query_one(DataTable).clear()
            self._sync_selection()
            status.update(str(e))
            return
        if self._busy or self._prompting or self.app.screen is not self:
            return
        table = self.query_one(DataTable)
        self._containers = {c.id: c for c in containers}
        table.clear()
        selected_row = 0
        for i, container in enumerate(containers):
            held_mb = sum(p.used_mb for p in hw.gpu_processes if p.container == container.name)
            table.add_row(container.name, container.state, container.owner,
                          container.cpu, container.memory,
                          f"{held_mb} MiB" if held_mb else "—",
                          "protected" if protection_reason(container, self.app.config) else "available",
                          key=container.id)
            if container.id == self._selected:
                selected_row = i
        self._selected = containers[selected_row].id if containers else None
        if containers:
            table.move_cursor(row=selected_row)
        status.update(f"{len(containers)} containers · ¹Visible local GPU compute processes only; — = not attributed. Refreshes every 5s." if containers
                      else "No containers found. Docker is available.")
        self._sync_selection()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._selected = str(event.row_key.value)
        self._sync_selection()

    def _sync_selection(self) -> None:
        container = self._containers.get(self._selected or "")
        for action in ("pause", "unpause", "stop", "start"):
            self.query_one(f"#resource-{action}", Button).disabled = (
                self._busy or container is None or bool(action_reason(container, action, self.app.config)))
        details = self.query_one("#resource-details", Static)
        if not container:
            details.update("Select a container to see controls.")
            return
        reason = protection_reason(container, self.app.config)
        details.update(f"{container.name} · {container.id[:12]} · {container.image}\n"
                       + (reason or ("Uses --rm: stop disabled because Docker would delete it."
                                     if container.auto_remove else
                                     "Stopped services stay stopped until you explicitly start them. Resume a paused service before stopping it.")))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = (event.button.id or "").removeprefix("resource-")
        if action == "refresh":
            self.action_refresh()
            return
        container = self._containers.get(self._selected or "")
        if not container or self._busy:
            return
        reason = action_reason(container, action, self.app.config)
        if reason:
            self.notify(reason, severity="warning")
            return
        descriptions = {
            "pause": "Suspend all processes. Allocated RAM/VRAM remains held; in-flight requests may time out.",
            "unpause": "Resume the suspended processes; this may introduce benchmark contention again.",
            "stop": "Terminate the service (10s graceful shutdown, then force). In-memory work is lost. The container remains available for Start.",
            "start": "Start the service and reload its processes/model; this may allocate GPU memory again.",
        }
        self._prompting = True

        def confirmed(answer: bool | None) -> None:
            self._prompting = False
            if answer:
                self._busy = True
                self._sync_selection()
                self._control(container.id, action)

        self.app.push_screen(ConfirmModal(
            f"{action.capitalize()} {container.name} ({container.id[:12]})?\n\n{descriptions[action]}",
            yes_label=action.capitalize(), no_label="Cancel"), confirmed)

    @work(group="resources-control")
    async def _control(self, container_id: str, action: str) -> None:
        try:
            await control_container(container_id, action, self.app.config)
            self.notify(f"{action.capitalize()} succeeded. Check GPU memory below before benchmarking.")
        except SandboxError as e:
            self.notify(str(e), severity="error", timeout=10)
        finally:
            self._busy = False
            self.action_refresh()
