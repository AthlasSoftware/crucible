"""Ollama backend: native /api endpoints for model listing and benchmark metrics
(exact token counts and durations), inheriting OpenAI-compatible /v1 chat —
Ollama v0.2x streams tool calls through /v1/chat/completions for capable models.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx

from crucible.backends.base import BackendError, GenResult, ModelInfo
from crucible.backends.openai_compat import OpenAICompatBackend

_NS = 1e9


class OllamaBackend(OpenAICompatBackend):
    name = "ollama"

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(f"ollama: cannot list models: {e}") from e
        models = []
        for m in resp.json().get("models", []):
            details = m.get("details") or {}
            models.append(
                ModelInfo(
                    id=m["name"],
                    size_bytes=m.get("size"),
                    family=details.get("family"),
                    quant=details.get("quantization_level"),
                )
            )
        return models

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        on_token: Callable[[str], None] | None = None,
        options: dict | None = None,
    ) -> GenResult:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            "options": {"num_predict": max_tokens, **(options or {})},
            "keep_alive": "5m",
        }
        start = time.monotonic()
        t_first = None
        chunks: list[str] = []
        offsets: list[float] = []
        final: dict = {}
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/api/generate", json=payload
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    raise BackendError(f"ollama: HTTP {resp.status_code}: {body[:300]}")
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise BackendError(f"ollama: {data['error']}")
                    token = data.get("response", "")
                    if token:
                        now = time.monotonic()
                        if t_first is None:
                            t_first = now
                        offsets.append(now - start)
                        chunks.append(token)
                        if on_token:
                            on_token(token)
                    if data.get("done"):
                        final = data
        except httpx.HTTPError as e:
            raise BackendError(f"ollama: streaming failed: {e}") from e

        end = time.monotonic()
        eval_duration = final.get("eval_duration", 0) / _NS
        return GenResult(
            text="".join(chunks),
            ttft_s=(t_first if t_first is not None else end) - start,
            prompt_tokens=final.get("prompt_eval_count", 0),
            gen_tokens=final.get("eval_count", len(chunks)),
            # Exact GPU/CPU eval time from the server; wall clock as fallback.
            gen_s=eval_duration or (end - (t_first or end)),
            total_s=end - start,
            prompt_eval_s=final.get("prompt_eval_duration", 0) / _NS or None,
            load_s=final.get("load_duration", 0) / _NS or None,
            approx=not final,
            chunk_offsets_s=offsets,
        )

    async def server_version(self) -> str | None:
        try:
            resp = await self._client.get(f"{self.base_url}/api/version")
            return resp.json().get("version")
        except (httpx.HTTPError, ValueError):
            return None

    async def unload(self, model: str) -> None:
        """Ask Ollama to free the model's VRAM immediately."""
        try:
            await self._client.post(
                f"{self.base_url}/api/generate",
                json={"model": model, "keep_alive": 0},
            )
        except httpx.HTTPError:
            pass  # best-effort: freeing VRAM is a nicety, not a failure mode
