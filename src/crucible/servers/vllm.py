"""A vLLM server CRUCIBLE spawns and owns.

vLLM is a single-model server: the model is fixed by `--model` at launch and
there is no API to swap it. So "switch the model" means "run a different
server", and that is what this is — a disposable vLLM container, one model at a
time, started and destroyed by CRUCIBLE.

It never touches a vLLM you started yourself. It runs on its own port, under
its own Docker label, and the release killswitch cleans it up with everything
else CRUCIBLE spawned. If another process is already holding the GPUs, spawning
refuses and says who — taking someone else's memory away is not ours to do.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx

from crucible import hardware
from crucible.config import VllmServerCfg
from crucible.sessions.docker import _docker, live_containers

SERVER_LABEL = "crucible.server"
_CONTAINER_PORT = 8000

# on_output(stream, line) where stream is "stdout" | "stderr" | "info"
OutputCb = Callable[[str, str], None]


class VllmServerError(Exception):
    pass


def _noop(_stream: str, _line: str) -> None:
    pass


def gpu_blockers() -> list[str]:
    """Human-readable reasons the GPUs are not ours to take, or an empty list.

    Deliberately generic: it reports whatever holds VRAM, by process and
    container name, without knowing or caring what that workload is.
    """
    hw = hardware.probe()
    if not hw.gpus:
        return []  # no NVIDIA GPUs visible; let vLLM be the one to complain
    return hardware.vram_holders(hw)


class VllmServer:
    """One spawned vLLM. Start it, use its base_url, destroy it."""

    def __init__(self, model: str, cfg: VllmServerCfg):
        self.model = model
        self.cfg = cfg
        # Named after the port rather than a random id: only one can hold the
        # port anyway, and a predictable name is easier to clean up by hand.
        self.name = f"crucible-vllm-{cfg.port}"
        self.alive = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.cfg.port}"

    async def start(self, emit: OutputCb = _noop) -> None:
        blockers = gpu_blockers()
        if blockers:
            raise VllmServerError(
                "the GPUs are already in use, so a vLLM would not fit:\n  "
                + "\n  ".join(blockers)
                + "\nFree them and try again — CRUCIBLE will not stop a process it "
                "did not start."
            )
        await self._remove_existing(emit)

        args = [
            "run", "-d", "--name", self.name,
            "--label", f"{SERVER_LABEL}={self.cfg.port}",
            "--gpus", "all",
            # vLLM's workers talk over shared memory; the default 64 MB is not
            # enough once tensor parallelism is on.
            "--ipc", "host",
            "-p", f"127.0.0.1:{self.cfg.port}:{_CONTAINER_PORT}",
            "-v", f"{self.cfg.hf_cache}:/root/.cache/huggingface",
        ]
        if self.cfg.hf_token:
            args += ["-e", f"HUGGING_FACE_HUB_TOKEN={self.cfg.hf_token}"]
        args += [self.cfg.image]
        args += self._model_args()

        emit("info", f"starting vLLM for {self.model} on port {self.cfg.port} …")
        code, out, err = await _docker(*args, timeout_s=180)
        if code != 0:
            raise VllmServerError(f"docker run failed: {err.strip() or out.strip()}")
        self.alive = True
        live_containers.add(self.name)
        try:
            await self._await_ready(emit)
        except Exception:
            # A half-started server holds VRAM and answers nothing; never leave
            # one behind for the user to discover later.
            await self.destroy()
            raise
        emit("info", f"vLLM ready at {self.base_url}")

    def _model_args(self) -> list[str]:
        args = [
            f"--model={self.model}",
            f"--served-model-name={self.model}",
            f"--tensor-parallel-size={self.cfg.tensor_parallel_size}",
            f"--gpu-memory-utilization={self.cfg.gpu_memory_utilization}",
            f"--max-model-len={self.cfg.max_model_len}",
            "--host=0.0.0.0",
            f"--port={_CONTAINER_PORT}",
        ]
        args.extend(a for a in self.cfg.extra_args if a)
        return args

    async def _remove_existing(self, emit: OutputCb) -> None:
        """Clear a container left behind by a previous run holding our name."""
        code, out, _ = await _docker(
            "ps", "-aq", "--filter", f"name=^{self.name}$", timeout_s=15
        )
        if code == 0 and out.strip():
            emit("info", f"removing an earlier {self.name}")
            await _docker("rm", "-f", self.name, timeout_s=60)

    async def _await_ready(self, emit: OutputCb) -> None:
        """Poll /v1/models until the model is loaded, or the container dies.

        Weight loading takes minutes, so a dead container has to end the wait
        early — otherwise a crash looks exactly like a slow start.
        """
        deadline = self.cfg.startup_timeout_s
        waited = 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            while waited < deadline:
                if not await self._container_running():
                    raise VllmServerError(
                        "the vLLM container exited during startup:\n"
                        + await self.logs_tail()
                    )
                try:
                    resp = await client.get(f"{self.base_url}/v1/models")
                    if resp.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(3)
                waited += 3
                if waited % 30 == 0:
                    emit("info", f"still loading … {waited}s of {deadline}s")
        raise VllmServerError(
            f"vLLM did not answer on {self.base_url} within {deadline}s:\n"
            + await self.logs_tail()
        )

    async def _container_running(self) -> bool:
        code, out, _ = await _docker(
            "inspect", "-f", "{{.State.Running}}", self.name, timeout_s=15
        )
        return code == 0 and out.strip() == "true"

    async def logs_tail(self, lines: int = 15) -> str:
        code, out, err = await _docker(
            "logs", "--tail", str(lines), self.name, timeout_s=30
        )
        if code != 0:
            return "(no logs available)"
        text = (err or out).strip()
        return text or "(no logs available)"

    async def destroy(self) -> None:
        await _docker("rm", "-f", self.name, timeout_s=60)
        self.alive = False
        live_containers.discard(self.name)

    @staticmethod
    async def count() -> int:
        code, out, _ = await _docker("ps", "-aq", "--filter", f"label={SERVER_LABEL}")
        return len(out.split()) if code == 0 else 0

    @staticmethod
    async def purge(exclude: set[str] | None = None) -> int:
        exclude = exclude or set()
        code, out, _ = await _docker(
            "ps", "-a", "--filter", f"label={SERVER_LABEL}", "--format", "{{.ID}} {{.Names}}"
        )
        if code != 0:
            return 0
        ids = []
        for line in out.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2 and parts[1].strip() not in exclude:
                ids.append(parts[0])
        if not ids:
            return 0
        await _docker("rm", "-f", *ids, timeout_s=120)
        return len(ids)
