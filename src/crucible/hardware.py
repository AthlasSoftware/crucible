"""Hardware probe: CPU/RAM/disk via psutil, NVIDIA GPUs via nvidia-smi CSV output.

probe() is cheap (<100ms) and safe to call on an interval; every GPU-less or
exotic system degrades to an empty gpu list rather than an error.

When a card is *not* idle we additionally ask nvidia-smi who is holding the
VRAM, and resolve containerized PIDs to their container name — "22.0G
VLLM::Worker_TP0 (kairos-vllm-active)" answers "why can't I load my model"
in one line. That second query only runs while something is actually
resident, so an idle machine costs exactly what it did before.
"""

from __future__ import annotations

import platform
import re
import subprocess
import time
from dataclasses import dataclass, field

import psutil

_NVIDIA_SMI_FIELDS = (
    "name",
    "memory.total",
    "memory.used",
    "utilization.gpu",
    "temperature.gpu",
    "driver_version",
    "uuid",
)

_NVIDIA_SMI_PROC_FIELDS = ("pid", "process_name", "used_gpu_memory", "gpu_uuid")

# A card counts as idle below both thresholds. Kept here (not in the UI) so the
# monitor's "● free" badge and the decision to hunt for processes agree.
IDLE_UTIL_PCT = 5
IDLE_VRAM_FRAC = 0.10

_CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}")
_CONTAINER_TTL_S = 30.0
_container_name_cache: dict[str, str] = {}
_container_cache_ts = 0.0


@dataclass
class GpuProcess:
    """A process holding VRAM on one GPU."""

    pid: int
    name: str
    used_mb: int
    gpu_uuid: str
    container: str | None = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.container})" if self.container else self.name


@dataclass
class GpuInfo:
    name: str
    vram_total_mb: int
    vram_used_mb: int
    util_pct: int
    temp_c: int
    driver: str
    uuid: str = ""

    @property
    def vram_frac(self) -> float:
        return self.vram_used_mb / self.vram_total_mb if self.vram_total_mb else 0.0

    @property
    def idle(self) -> bool:
        return self.util_pct < IDLE_UTIL_PCT and self.vram_frac < IDLE_VRAM_FRAC


@dataclass
class HardwareInfo:
    cpu_model: str
    cores_physical: int
    cores_logical: int
    freq_mhz: float
    load_pct: float
    ram_total_gb: float
    ram_used_gb: float
    disk_total_gb: float
    disk_free_gb: float
    os_name: str
    gpus: list[GpuInfo] = field(default_factory=list)
    gpu_processes: list[GpuProcess] = field(default_factory=list)

    @property
    def gpus_idle(self) -> bool:
        """True when every card is idle (or there are no cards at all)."""
        return all(g.idle for g in self.gpus)

    def processes_on(self, gpu: GpuInfo) -> list[GpuProcess]:
        """VRAM holders on one card, biggest first."""
        procs = [p for p in self.gpu_processes if p.gpu_uuid == gpu.uuid]
        return sorted(procs, key=lambda p: p.used_mb, reverse=True)


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine() or "unknown"


def _os_name() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}"


def _run_nvidia_smi() -> str:
    """Isolated for monkeypatching in tests. Raises on any failure."""
    return subprocess.run(
        [
            "nvidia-smi",
            f"--query-gpu={','.join(_NVIDIA_SMI_FIELDS)}",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    ).stdout


def _run_nvidia_smi_procs() -> str:
    """Isolated for monkeypatching in tests. Raises on any failure."""
    return subprocess.run(
        [
            "nvidia-smi",
            f"--query-compute-apps={','.join(_NVIDIA_SMI_PROC_FIELDS)}",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    ).stdout


def _run_docker_ps() -> str:
    """Isolated for monkeypatching in tests. Raises on any failure."""
    return subprocess.run(
        ["docker", "ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"],
        capture_output=True,
        text=True,
        timeout=3,
        check=True,
    ).stdout


def probe_gpus() -> list[GpuInfo]:
    try:
        output = _run_nvidia_smi()
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != len(_NVIDIA_SMI_FIELDS):
            continue
        try:
            gpus.append(
                GpuInfo(
                    name=parts[0],
                    vram_total_mb=int(float(parts[1])),
                    vram_used_mb=int(float(parts[2])),
                    util_pct=int(float(parts[3])),
                    temp_c=int(float(parts[4])),
                    driver=parts[5],
                    uuid=parts[6],
                )
            )
        except ValueError:
            continue
    return gpus


def _container_id_for_pid(pid: int) -> str | None:
    """Full container id from a PID's cgroup, or None if it isn't containerized.

    Matches both cgroup v2 (`0::/system.slice/docker-<id>.scope`) and v1
    (`…/docker/<id>`) layouts by simply hunting for the 64-hex id.
    """
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
            match = _CONTAINER_ID_RE.search(f.read())
    except OSError:
        return None
    return match.group(0) if match else None


def _container_names(refresh: bool = False) -> dict[str, str]:
    """id → name for running containers, cached; empty dict when docker is absent."""
    global _container_cache_ts
    now = time.monotonic()
    if not refresh and _container_name_cache and now - _container_cache_ts < _CONTAINER_TTL_S:
        return _container_name_cache
    try:
        output = _run_docker_ps()
    except (OSError, subprocess.SubprocessError):
        return {}
    names = {}
    for line in output.strip().splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            names[parts[0]] = parts[1]
    _container_name_cache.clear()
    _container_name_cache.update(names)
    _container_cache_ts = now
    return _container_name_cache


def probe_gpu_processes() -> list[GpuProcess]:
    """Who is holding VRAM right now. Empty on any failure, or when nothing is."""
    try:
        output = _run_nvidia_smi_procs()
    except (OSError, subprocess.SubprocessError):
        return []
    procs = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != len(_NVIDIA_SMI_PROC_FIELDS):
            continue
        try:
            pid, used_mb = int(parts[0]), int(float(parts[2]))
        except ValueError:
            continue  # "[N/A]" for MIG / permission-limited rows
        procs.append(
            GpuProcess(pid=pid, name=parts[1].split("/")[-1], used_mb=used_mb, gpu_uuid=parts[3])
        )
    # One docker call for the whole batch, and only when something is containerized.
    ids = {p.pid: cid for p in procs if (cid := _container_id_for_pid(p.pid))}
    if ids:
        names = _container_names()
        for proc in procs:
            proc.container = names.get(ids.get(proc.pid, ""))
    return procs


def status_line(hw: HardwareInfo) -> str:
    """Compact one-line readout (plain text, no markup) for live monitors."""
    parts = [
        f"CPU {hw.load_pct:3.0f}%",
        f"RAM {hw.ram_used_gb:.1f}/{hw.ram_total_gb:.0f}G",
    ]
    parts.extend(
        f"GPU{i} {g.util_pct:3d}% {g.vram_used_mb / 1024:.1f}/{g.vram_total_mb / 1024:.0f}G "
        f"{g.temp_c}°C"
        for i, g in enumerate(hw.gpus)
    )
    return "  ·  ".join(parts)


def vram_holders(hw: HardwareInfo) -> list[str]:
    """One plain-text line per busy card naming what holds its VRAM.

    Empty while the GPUs are idle — this readout only earns its space when
    something is in the way.
    """
    lines = []
    for i, gpu in enumerate(hw.gpus):
        if gpu.idle:
            continue
        procs = hw.processes_on(gpu)
        if not procs:
            # Busy but nothing attributable: compute apps on another user, or a
            # driver-level allocation we cannot see into.
            lines.append(f"GPU{i}: {gpu.vram_used_mb / 1024:.1f}G in use, no visible process")
            continue
        held = ", ".join(f"{p.used_mb / 1024:.1f}G {p.label}" for p in procs)
        lines.append(f"GPU{i}: {held}")
    return lines


def probe() -> HardwareInfo:
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    freq = psutil.cpu_freq()
    gpus = probe_gpus()
    # Only go looking for VRAM holders when a card is actually busy: on an idle
    # machine this keeps probe() to the single nvidia-smi call it always was.
    processes = probe_gpu_processes() if any(not g.idle for g in gpus) else []
    return HardwareInfo(
        cpu_model=_cpu_model(),
        cores_physical=psutil.cpu_count(logical=False) or 0,
        cores_logical=psutil.cpu_count(logical=True) or 0,
        freq_mhz=freq.current if freq else 0.0,
        load_pct=psutil.cpu_percent(interval=None),
        ram_total_gb=ram.total / 1e9,
        ram_used_gb=ram.used / 1e9,
        disk_total_gb=disk.total / 1e9,
        disk_free_gb=disk.free / 1e9,
        os_name=_os_name(),
        gpus=gpus,
        gpu_processes=processes,
    )
