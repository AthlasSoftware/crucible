"""GPU occupancy readout: parsing, the idle rule, and container attribution."""

import pytest

from crucible import hardware

_GPU_CSV = (
    "NVIDIA GeForce RTX 3090, 24576, 22055, 0, 45, 580.65, GPU-aaa\n"
    "NVIDIA GeForce RTX 3090, 24576, 1200, 0, 38, 580.65, GPU-bbb\n"
)
_PROC_CSV = (
    "7168, VLLM::Worker_TP0, 22032, GPU-aaa\n"
    "9001, /usr/bin/python3, 900, GPU-bbb\n"
)


@pytest.fixture
def no_container_lookup(monkeypatch):
    monkeypatch.setattr(hardware, "_container_id_for_pid", lambda _pid: None)


def test_parses_uuid_and_idle_rule(monkeypatch):
    monkeypatch.setattr(hardware, "_run_nvidia_smi", lambda: _GPU_CSV)
    busy, quiet = hardware.probe_gpus()
    assert busy.uuid == "GPU-aaa"
    assert busy.vram_frac == pytest.approx(22055 / 24576)
    # 90% VRAM at 0% utilization is still occupied — a loaded model just sitting
    # there is exactly the case this readout exists for.
    assert not busy.idle
    assert quiet.idle


def test_process_name_is_basename(monkeypatch, no_container_lookup):
    monkeypatch.setattr(hardware, "_run_nvidia_smi_procs", lambda: _PROC_CSV)
    procs = hardware.probe_gpu_processes()
    assert [p.name for p in procs] == ["VLLM::Worker_TP0", "python3"]
    assert [p.used_mb for p in procs] == [22032, 900]
    assert all(p.container is None for p in procs)


def test_unparseable_rows_are_skipped(monkeypatch, no_container_lookup):
    monkeypatch.setattr(
        hardware,
        "_run_nvidia_smi_procs",
        lambda: "[N/A], python3, [N/A], GPU-aaa\n7168, real, 100, GPU-aaa\n",
    )
    assert [p.pid for p in hardware.probe_gpu_processes()] == [7168]


def test_missing_nvidia_smi_degrades_to_empty(monkeypatch):
    def boom():
        raise OSError("no nvidia-smi here")

    monkeypatch.setattr(hardware, "_run_nvidia_smi", boom)
    monkeypatch.setattr(hardware, "_run_nvidia_smi_procs", boom)
    assert hardware.probe_gpus() == []
    assert hardware.probe_gpu_processes() == []


def test_container_name_resolved_from_cgroup(monkeypatch):
    cid = "e" * 64
    monkeypatch.setattr(hardware, "_run_nvidia_smi_procs", lambda: "7168, py, 100, GPU-aaa\n")
    monkeypatch.setattr(hardware, "_container_id_for_pid", lambda _pid: cid)
    monkeypatch.setattr(hardware, "_run_docker_ps", lambda: f"{cid} kairos-vllm-active\n")
    hardware._container_name_cache.clear()
    proc = hardware.probe_gpu_processes()[0]
    assert proc.container == "kairos-vllm-active"
    assert proc.label == "py (kairos-vllm-active)"


def test_docker_absent_leaves_plain_process_name(monkeypatch):
    def boom():
        raise FileNotFoundError("docker")

    monkeypatch.setattr(hardware, "_run_nvidia_smi_procs", lambda: "7168, py, 100, GPU-aaa\n")
    monkeypatch.setattr(hardware, "_container_id_for_pid", lambda _pid: "f" * 64)
    monkeypatch.setattr(hardware, "_run_docker_ps", boom)
    hardware._container_name_cache.clear()
    proc = hardware.probe_gpu_processes()[0]
    assert proc.container is None
    assert proc.label == "py"


def test_probe_skips_process_query_when_all_idle(monkeypatch):
    """The whole point of the feature: an idle machine pays nothing for it."""
    called = []
    monkeypatch.setattr(
        hardware, "_run_nvidia_smi", lambda: "RTX 3090, 24576, 100, 0, 30, 580, GPU-bbb\n"
    )
    monkeypatch.setattr(
        hardware, "_run_nvidia_smi_procs", lambda: called.append(1) or ""
    )
    hw = hardware.probe()
    assert hw.gpus_idle
    assert hw.gpu_processes == []
    assert called == []


def test_probe_collects_processes_when_busy(monkeypatch, no_container_lookup):
    monkeypatch.setattr(hardware, "_run_nvidia_smi", lambda: _GPU_CSV)
    monkeypatch.setattr(hardware, "_run_nvidia_smi_procs", lambda: _PROC_CSV)
    hw = hardware.probe()
    assert not hw.gpus_idle
    busy = hw.gpus[0]
    assert [p.pid for p in hw.processes_on(busy)] == [7168]


def test_vram_holders_only_lists_busy_cards(monkeypatch, no_container_lookup):
    monkeypatch.setattr(hardware, "_run_nvidia_smi", lambda: _GPU_CSV)
    monkeypatch.setattr(hardware, "_run_nvidia_smi_procs", lambda: _PROC_CSV)
    lines = hardware.vram_holders(hardware.probe())
    assert len(lines) == 1  # GPU1 is idle and says nothing
    assert lines[0].startswith("GPU0:")
    assert "VLLM::Worker_TP0" in lines[0]


def test_vram_holders_is_empty_when_idle(monkeypatch):
    monkeypatch.setattr(
        hardware, "_run_nvidia_smi", lambda: "RTX 3090, 24576, 100, 0, 30, 580, GPU-bbb\n"
    )
    assert hardware.vram_holders(hardware.probe()) == []


def test_busy_card_without_visible_process_still_reports(monkeypatch, no_container_lookup):
    monkeypatch.setattr(hardware, "_run_nvidia_smi", lambda: _GPU_CSV)
    monkeypatch.setattr(hardware, "_run_nvidia_smi_procs", lambda: "")
    lines = hardware.vram_holders(hardware.probe())
    assert "no visible process" in lines[0]


def test_processes_on_sorts_by_size(monkeypatch, no_container_lookup):
    monkeypatch.setattr(hardware, "_run_nvidia_smi", lambda: _GPU_CSV)
    monkeypatch.setattr(
        hardware,
        "_run_nvidia_smi_procs",
        lambda: "1, small, 100, GPU-aaa\n2, big, 9000, GPU-aaa\n",
    )
    hw = hardware.probe()
    assert [p.name for p in hw.processes_on(hw.gpus[0])] == ["big", "small"]
