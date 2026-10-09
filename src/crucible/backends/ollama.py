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
        reasoning_observed = False
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
                    reasoning_observed = reasoning_observed or bool(data.get("thinking"))
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
        if not final:
            raise BackendError("ollama: stream ended without final metrics; result incomplete")
        if not chunks:
            raise BackendError("ollama: no visible output; TTFT unavailable. A reasoning model may need a higher token cap.")
        eval_duration = final.get("eval_duration", 0) / _NS
        exact = (eval_duration > 0 and final.get("prompt_eval_duration", 0) > 0
                 and "eval_count" in final and "prompt_eval_count" in final)
        return GenResult(
            text="".join(chunks),
            ttft_s=(t_first if t_first is not None else end) - start,
            prompt_tokens=final.get("prompt_eval_count", 0),
            gen_tokens=final.get("eval_count", len(chunks)),
            # Exact GPU/CPU eval time from the server; wall clock as fallback.
            gen_s=eval_duration or (end - (t_first or end)),
            total_s=offsets[-1] if offsets else end - start,
            prompt_eval_s=final.get("prompt_eval_duration", 0) / _NS or None,
            load_s=final.get("load_duration", 0) / _NS or None,
            approx=not exact,
            chunk_offsets_s=offsets,
            timing_source="server" if eval_duration > 0 else "client",
            token_count_source="server" if "eval_count" in final else "chunks",
            finish_reason=final.get("done_reason"),
            cached_prompt_tokens=final.get("prompt_eval_cached_count"),
            reasoning_observed=reasoning_observed,
        )

    async def server_version(self) -> str | None:
        try:
            resp = await self._client.get(f"{self.base_url}/api/version")
            return resp.json().get("version")
        except (httpx.HTTPError, ValueError):
            return None

    async def model_info(self, model: str) -> dict:
        """Provenance from /api/show: family/quant/size, template hash, max context."""
        import hashlib

        try:
            resp = await self._client.post(f"{self.base_url}/api/show", json={"model": model})
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            return {}
        details = data.get("details") or {}
        template = data.get("template") or ""
        arch_info = data.get("model_info") or {}

        def arch(suffix: str):
            return next((v for k, v in arch_info.items() if k.endswith(suffix)), None)

        context_length = arch(".context_length")
        return {
            "n_layers": arch(".block_count"),
            "n_heads": arch(".attention.head_count"),
            "n_kv_heads": arch(".attention.head_count_kv"),
            "embedding_length": arch(".embedding_length"),
            "head_dim": arch(".attention.key_length"),  # explicit on some archs

            "family": details.get("family"),
            "quant": details.get("quantization_level"),
            "param_size": details.get("parameter_size"),
            "template": template,
            "template_sha256": hashlib.sha256(template.encode()).hexdigest()[:12]
            if template else None,
            "parameters": data.get("parameters") or "",
            "model_context_length": context_length,
        }

    async def unload(self, model: str) -> None:
        """Ask Ollama to free the model's VRAM immediately."""
        try:
            await self._client.post(
                f"{self.base_url}/api/generate",
                json={"model": model, "keep_alive": 0},
            )
        except httpx.HTTPError:
            pass  # best-effort: freeing VRAM is a nicety, not a failure mode
