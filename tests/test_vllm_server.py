"""The vLLM server CRUCIBLE spawns: launch args, and the refusal to take GPUs."""

import pytest

from crucible import hardware
from crucible.config import Config, VllmServerCfg
from crucible.servers import VllmServer, VllmServerError
from crucible.servers import vllm as vllm_mod


def _cfg(**kwargs):
    return VllmServerCfg(**kwargs)


def test_defaults_are_self_contained():
    """A fresh install must not assume any server or cache already exists."""
    cfg = Config().vllm_server
    assert cfg.enabled is False  # spawning is opt-in
    assert cfg.hf_cache == "crucible-hf-cache"  # our own volume, not someone else's
    assert cfg.port != 8000  # never the port a hand-started vLLM usually takes


def test_port_is_configurable_and_names_the_container():
    server = VllmServer("org/model", _cfg(port=8111))
    assert server.base_url == "http://127.0.0.1:8111"
    assert "8111" in server.name


def test_launch_args_carry_the_model_and_limits():
    server = VllmServer("org/model", _cfg(tensor_parallel_size=2,
                                          gpu_memory_utilization=0.8,
                                          max_model_len=4096))
    args = server._model_args()
    assert "--model=org/model" in args
    assert "--served-model-name=org/model" in args
    assert "--tensor-parallel-size=2" in args
    assert "--gpu-memory-utilization=0.8" in args
    assert "--max-model-len=4096" in args


def test_extra_args_are_appended():
    server = VllmServer("org/model", _cfg(extra_args=["--quantization=awq", ""]))
    args = server._model_args()
    assert "--quantization=awq" in args
    assert "" not in args  # blank entries from a hand-edited TOML are dropped


def test_gpu_blockers_empty_when_cards_are_idle(monkeypatch):
    monkeypatch.setattr(
        hardware, "_run_nvidia_smi", lambda: "RTX 3090, 24576, 100, 0, 30, 580, GPU-a\n"
    )
    assert vllm_mod.gpu_blockers() == []


def test_gpu_blockers_names_the_holder(monkeypatch):
    monkeypatch.setattr(
        hardware, "_run_nvidia_smi", lambda: "RTX 3090, 24576, 22000, 0, 45, 580, GPU-a\n"
    )
    monkeypatch.setattr(
        hardware, "_run_nvidia_smi_procs", lambda: "42, some-server, 21900, GPU-a\n"
    )
    monkeypatch.setattr(hardware, "_container_id_for_pid", lambda _pid: None)
    blockers = vllm_mod.gpu_blockers()
    assert len(blockers) == 1
    assert "some-server" in blockers[0]


def test_no_gpus_at_all_is_not_treated_as_a_blocker(monkeypatch):
    """A CPU-only box should get vLLM's own error, not ours."""
    monkeypatch.setattr(hardware, "_run_nvidia_smi", lambda: "")
    assert vllm_mod.gpu_blockers() == []


async def test_start_refuses_when_something_else_holds_the_gpus(monkeypatch):
    monkeypatch.setattr(vllm_mod, "gpu_blockers", lambda: ["GPU0: 21.4G some-server"])

    async def fail(*_args, **_kwargs):
        raise AssertionError("docker must not be touched when we refuse")

    monkeypatch.setattr(vllm_mod, "_docker", fail)
    server = VllmServer("org/model", _cfg())
    with pytest.raises(VllmServerError) as excinfo:
        await server.start()
    message = str(excinfo.value)
    assert "some-server" in message  # says what is in the way
    assert "will not stop a process it did not start" in message
    assert server.alive is False


async def test_failed_startup_destroys_the_container(monkeypatch):
    """A half-started server holds VRAM and answers nothing — never leave one."""
    monkeypatch.setattr(vllm_mod, "gpu_blockers", lambda: [])
    removed = []

    async def fake_docker(*args, **_kwargs):
        if args[0] == "run":
            return 0, "cid", ""
        if args[0] == "rm":
            removed.append(args)
            return 0, "", ""
        if args[0] == "ps":
            return 0, "", ""
        if args[0] == "inspect":
            return 0, "false", ""  # container died during startup
        if args[0] == "logs":
            return 0, "boom", ""
        return 0, "", ""

    monkeypatch.setattr(vllm_mod, "_docker", fake_docker)
    server = VllmServer("org/model", _cfg())
    with pytest.raises(VllmServerError):
        await server.start()
    assert removed, "the dead container was left behind"
    assert server.alive is False


async def test_purge_matches_on_name_not_id(monkeypatch):
    calls = {}

    async def fake_docker(*args, **_kwargs):
        if args[0] == "ps":
            return 0, "abc123 crucible-vllm-8100\ndef456 crucible-vllm-8111\n", ""
        if args[0] == "rm":
            calls["removed"] = args[2:]
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(vllm_mod, "_docker", fake_docker)
    count = await VllmServer.purge(exclude={"crucible-vllm-8100"})
    assert count == 1
    assert calls["removed"] == ("def456",)
