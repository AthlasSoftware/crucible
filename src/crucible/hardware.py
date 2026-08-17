"""Hardware probe: CPU/RAM/disk via psutil, NVIDIA GPUs via nvidia-smi CSV output.

probe() is cheap (<100ms) and safe to call on an interval; every GPU-less or
exotic system degrades to an empty gpu list rather than an error.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass, field

import psutil

_NVIDIA_SMI_FIELDS = (
    "name",
    "memory.total",
    "memory.used",
    "utilization.gpu",
    "temperature.gpu",
    "driver_version",
)


@dataclass
class GpuInfo:
    name: str
    vram_total_mb: int
    vram_used_mb: int
    util_pct: int
    temp_c: int
    driver: str


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
                )
            )
        except ValueError:
            continue
    return gpus


def probe() -> HardwareInfo:
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    freq = psutil.cpu_freq()
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
        gpus=probe_gpus(),
    )
