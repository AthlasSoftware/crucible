import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from crucible import resources
from crucible.config import Config
from crucible.resources import ContainerInfo, action_reason, control_container, protection_reason
from crucible.sessions.docker import SandboxError, live_containers


def container(**kwargs):
    return ContainerInfo(id="a" * 64, name="other-service", state="running", **kwargs)


def metadata(item):
    return json.dumps({"id": item.id, "name": "/" + item.name, "state": item.state,
                       "auto_remove": item.auto_remove, "ports": item.ports})


def test_protects_endpoint_ports_and_current_process_resources():
    cfg = Config()
    item = container(ports={"8000/tcp": [{"HostPort": "11434"}]})
    assert "inference endpoint" in protection_reason(item, cfg)
    live_containers.add(item.name)
    try:
        assert "owned by this Crucible" in protection_reason(item, cfg)
    finally:
        live_containers.discard(item.name)
    assert protection_reason(container(), cfg) == ""


def test_remote_endpoint_is_not_mistaken_for_local_port():
    cfg = Config()
    cfg.backend.ollama.base_url = "https://remote.example:11434"
    assert protection_reason(container(ports={"11434/tcp": [{"HostPort": "11434"}]}), cfg) == ""


def test_stop_refuses_auto_remove_and_paused_state():
    assert "--rm" in action_reason(container(auto_remove=True), "stop", Config())
    assert "paused" in action_reason(replace(container(), state="paused"), "stop", Config())
    assert action_reason(replace(container(), state="paused"), "unpause", Config()) == ""


@pytest.mark.parametrize("action,state", [("pause", "running"), ("unpause", "paused"),
                                         ("stop", "running"), ("start", "exited")])
async def test_controls_exact_id_without_removing_or_shell_execution(monkeypatch, action, state):
    item = replace(container(), state=state)
    calls = []

    async def docker(*args, **kwargs):
        calls.append(args)
        return (0, metadata(item), "") if args[0] == "inspect" else (0, "", "")

    monkeypatch.setattr(resources, "_docker", docker)
    await control_container(item.id, action, Config())
    assert calls[0][0] == "inspect"
    assert calls[1] == (("stop", "--timeout", "10", item.id) if action == "stop" else (action, item.id))
    assert all(c[0] not in ("rm", "kill", "exec") for c in calls)


async def test_revalidates_protection_after_user_confirmation(monkeypatch):
    item = container(ports={"8000/tcp": [{"HostPort": "8000"}]})
    calls = []

    async def docker(*args, **kwargs):
        calls.append(args)
        return 0, metadata(item), ""

    monkeypatch.setattr(resources, "_docker", docker)
    with pytest.raises(SandboxError, match="inference endpoint"):
        await control_container(item.id, "stop", Config())
    assert len(calls) == 1  # only inspect, no mutation


async def test_failed_action_reports_docker_error(monkeypatch):
    async def docker(*args, **kwargs):
        return (0, metadata(container()), "") if args[0] == "inspect" else (1, "", "access denied")

    monkeypatch.setattr(resources, "_docker", docker)
    with pytest.raises(SandboxError, match="access denied"):
        await control_container(container().id, "pause", Config())


async def test_unavailable_docker_is_not_a_clean_host(monkeypatch):
    async def docker(*args, **kwargs):
        return 127, "", "not installed"

    monkeypatch.setattr(resources, "_docker", docker)
    assert (await resources.container_snapshot())["status"] == "unavailable"


def test_snapshot_and_inspect_do_not_expose_environment_or_arbitrary_labels():
    item = container(labels={"private.key": "secret"})
    assert "labels" not in item.snapshot()
    assert ".Env" not in resources._INSPECT_FORMAT


async def test_cancelled_docker_client_already_exited_does_not_crash(monkeypatch):
    from crucible.sessions import docker

    communicate = AsyncMock(side_effect=[asyncio.CancelledError(), (b"", b"")])
    proc = SimpleNamespace(communicate=communicate, kill=Mock(side_effect=ProcessLookupError),
                           returncode=0)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    with pytest.raises(asyncio.CancelledError):
        await docker._docker("ps")
    assert communicate.await_count == 2
