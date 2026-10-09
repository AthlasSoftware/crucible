"""Explicit container controls, with identity/state revalidation before action.

No automatic stop/pause, no shell commands, no container deletion. Discovery
never reads container environment variables or writes them into reports.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from crucible.config import BACKEND_NAMES, Config
from crucible.sessions.docker import SandboxError, _docker, live_containers

_INSPECT_FORMAT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"state":{{json .State.Status}},'
    '"image":{{json .Config.Image}},"labels":{{json .Config.Labels}},'
    '"auto_remove":{{json .HostConfig.AutoRemove}},'
    '"ports":{{json .HostConfig.PortBindings}}}'
)


@dataclass
class ContainerInfo:
    id: str
    name: str
    state: str
    image: str = ""
    labels: dict = field(default_factory=dict)
    auto_remove: bool = False
    ports: dict = field(default_factory=dict)
    cpu: str = "unavailable"
    memory: str = "unavailable"

    @property
    def owner(self) -> str:
        return "Crucible" if any(k in self.labels for k in (
            "crucible.session", "crucible.harness", "crucible.server")) else "external"

    def snapshot(self) -> dict:
        # Keep only attribution/state, not arbitrary application labels.
        return {k: v for k, v in asdict(self).items()
                if k in ("id", "name", "state", "image")}


def parse_containers(output: str) -> list[ContainerInfo]:
    containers = []
    for line in output.splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        containers.append(ContainerInfo(
            id=data["id"], name=data["name"].lstrip("/"), state=data["state"],
            image=data.get("image", ""), labels=data.get("labels") or {},
            auto_remove=data.get("auto_remove", False), ports=data.get("ports") or {},
        ))
    return containers


async def list_containers(*, stats: bool = False) -> list[ContainerInfo]:
    code, out, err = await _docker("ps", "-aq", "--no-trunc", timeout_s=10)
    if code:
        raise SandboxError(f"Docker unavailable: {err.strip() or out.strip()}")
    ids = out.split()
    if not ids:
        return []
    code, out, err = await _docker("inspect", "--format", _INSPECT_FORMAT, *ids, timeout_s=15)
    if code:
        raise SandboxError(f"Container list changed or inspect failed; refresh: {err.strip()}")
    try:
        containers = parse_containers(out)
    except (ValueError, KeyError, TypeError) as e:
        raise SandboxError("Docker returned unreadable container metadata") from e
    running = [c.id for c in containers if c.state == "running"]
    if stats and running:
        try:
            code, out, _ = await _docker(
                "stats", "--no-stream", "--no-trunc", "--format", "{{json .}}", *running,
                timeout_s=15,
            )
        except SandboxError:
            code, out = 1, ""  # failed telemetry must not hide controllable containers
        if not code:
            by_id = {c.id: c for c in containers}
            for line in out.splitlines():
                try:
                    row = json.loads(line)
                    container = by_id.get(row.get("ID"))
                    if container:
                        container.cpu = row.get("CPUPerc", "unavailable")
                        container.memory = row.get("MemUsage", "unavailable")
                except (ValueError, TypeError):
                    continue
    return sorted(containers, key=lambda c: (c.state not in ("running", "paused"), c.name))


def protection_reason(container: ContainerInfo, cfg: Config) -> str:
    if container.name in live_containers:
        return "Active resource owned by this Crucible process. Release it from its owning screen."
    # Protect every configured local endpoint, including one selected in a
    # screen but not saved as the global default. Remote hosts cannot be mapped.
    ports = set()
    for name in BACKEND_NAMES:
        try:
            url = urlsplit(cfg.backend.endpoint(name).base_url)
            if url.hostname in ("localhost", "127.0.0.1", "::1"):
                ports.add(str(url.port or (443 if url.scheme == "https" else 80)))
        except ValueError:
            continue
    if cfg.vllm_server.enabled:
        ports.add(str(cfg.vllm_server.port))
    if any(binding.get("HostPort") in ports for bindings in container.ports.values()
           for binding in (bindings or [])):
        return "Publishes a configured local inference endpoint. Change the endpoint in Config before controlling this service."
    return ""


def action_reason(container: ContainerInfo, action: str, cfg: Config) -> str:
    if action not in ("pause", "unpause", "stop", "start"):
        return "Unknown container action."
    protected = protection_reason(container, cfg)
    if protected:
        return protected
    required = {"pause": ("running",), "unpause": ("paused",),
                "stop": ("running",), "start": ("exited", "created")}
    if container.state not in required[action]:
        return f"Cannot {action} a {container.state} container. Refresh its state first."
    if action == "stop" and container.auto_remove:
        return "This container uses --rm: stopping deletes it. Pause it or manage it outside Crucible."
    return ""


async def control_container(container_id: str, action: str, cfg: Config) -> None:
    if action not in ("pause", "unpause", "stop", "start"):
        raise SandboxError("Unknown container action")
    # Full IDs prevent accidental name reuse or a refresh changing the target.
    code, out, err = await _docker("inspect", "--format", _INSPECT_FORMAT, container_id,
                                   timeout_s=15)
    if code:
        raise SandboxError(f"Container no longer available: {err.strip()}")
    try:
        current = parse_containers(out)[0]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise SandboxError("Could not revalidate the selected container") from e
    if current.id != container_id:
        raise SandboxError("Container identity changed; refresh and select again")
    reason = action_reason(current, action, cfg)
    if reason:
        raise SandboxError(reason)
    args = ("stop", "--timeout", "10", container_id) if action == "stop" else (action, container_id)
    code, out, err = await _docker(*args, timeout_s=30)
    if code:
        raise SandboxError(f"Docker {action} failed: {err.strip() or out.strip()}")


async def container_snapshot() -> dict:
    try:
        return {"status": "observed", "containers": [c.snapshot() for c in await list_containers()]}
    except SandboxError as e:
        return {"status": "unavailable", "reason": str(e)}
