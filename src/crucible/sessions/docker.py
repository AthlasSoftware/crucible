"""Disposable Docker sandboxes driven through the docker CLI.

All invocations use asyncio.create_subprocess_exec — never a shell — and
commands run inside the container under coreutils `timeout`, because killing
the local `docker exec` client does not kill the containerized process.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass

LABEL = "crucible.session"
OUTPUT_CAP_BYTES = 256 * 1024

# on_output(stream, line) where stream is "stdout" | "stderr" | "info"
OutputCb = Callable[[str, str], None]


class SandboxError(Exception):
    pass


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False

    @property
    def timed_out(self) -> bool:
        return self.exit_code == 124  # coreutils timeout convention


async def _docker(*args: str, timeout_s: float = 60.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "docker", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        proc.kill()
        raise SandboxError(f"docker {' '.join(args[:2])} timed out") from None
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


class DockerSandbox:
    def __init__(self, image: str, cpus: float = 4.0, memory: str = "4g",
                 network: bool = False):
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.network = network
        self.id = uuid.uuid4().hex[:8]
        self.name = f"crucible-session-{self.id}"
        self.alive = False

    async def create(self, on_output: OutputCb | None = None) -> None:
        """Start the container; pulls the image first (streaming progress) if needed."""
        emit = on_output or (lambda _s, _l: None)
        code, _, _ = await _docker("image", "inspect", self.image, timeout_s=15)
        if code != 0:
            emit("info", f"pulling image {self.image} ...")
            await self._pull(emit)
        args = [
            "run", "-d", "--name", self.name,
            "--label", f"{LABEL}={self.id}",
            "--cpus", str(self.cpus), "--memory", self.memory,
        ]
        if not self.network:
            args += ["--network", "none"]
        args += [self.image, "sleep", "infinity"]
        code, out, err = await _docker(*args, timeout_s=120)
        if code != 0:
            raise SandboxError(f"docker run failed: {err.strip() or out.strip()}")
        self.alive = True
        emit("info", f"container {self.name} up ({self.image})")

    async def _pull(self, emit: OutputCb) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker", "pull", self.image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if line:
                emit("info", line)
        await proc.wait()
        if proc.returncode != 0:
            raise SandboxError(f"docker pull {self.image} failed")

    async def exec(
        self,
        command: str,
        timeout_s: int = 60,
        on_output: OutputCb | None = None,
    ) -> ExecResult:
        """Run a shell command in the container, streaming lines to on_output.

        Output collection is capped at OUTPUT_CAP_BYTES per stream; the rest is
        drained and discarded so the pipe never blocks.
        """
        if not self.alive:
            raise SandboxError("sandbox is not running")
        emit = on_output or (lambda _s, _l: None)
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", self.name,
            "timeout", f"{timeout_s}s", "sh", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        truncated = False

        async def read(stream, label: str) -> str:
            nonlocal truncated
            collected: list[bytes] = []
            size = 0
            while True:
                try:
                    raw = await stream.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    # line longer than the stream reader limit: pull a chunk instead
                    raw = await stream.read(64 * 1024)
                if not raw:
                    break
                if size < OUTPUT_CAP_BYTES:
                    collected.append(raw)
                    size += len(raw)
                    emit(label, raw.decode(errors="replace").rstrip("\n"))
                else:
                    truncated = True  # keep draining, drop the bytes
            return b"".join(collected).decode(errors="replace")

        stdout, stderr = await asyncio.gather(
            read(proc.stdout, "stdout"), read(proc.stderr, "stderr")
        )
        # The in-container `timeout` bounds runtime; +15s of grace for docker itself.
        try:
            code = await asyncio.wait_for(proc.wait(), timeout=timeout_s + 15)
        except TimeoutError:
            proc.kill()
            code = 124
        if truncated:
            emit("info", f"[output capped at {OUTPUT_CAP_BYTES // 1024} KiB]")
        return ExecResult(exit_code=code, stdout=stdout, stderr=stderr, truncated=truncated)

    async def destroy(self) -> None:
        await _docker("rm", "-f", self.name, timeout_s=30)
        self.alive = False

    @staticmethod
    async def purge_stale(exclude: set[str] | None = None) -> int:
        """Remove crucible session containers, optionally sparing live session ids."""
        exclude = exclude or set()
        code, out, _ = await _docker(
            "ps", "-a", "--filter", f"label={LABEL}", "--format", "{{.ID}} {{.Names}}"
        )
        if code != 0:
            return 0
        ids = [
            line.split()[0]
            for line in out.splitlines()
            if line.strip() and not any(f"crucible-session-{x}" in line for x in exclude)
        ]
        if not ids:
            return 0
        await _docker("rm", "-f", *ids, timeout_s=60)
        return len(ids)
