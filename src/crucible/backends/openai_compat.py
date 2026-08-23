"""OpenAI-compatible backend over /v1 — serves vLLM, generic OpenAI-style servers,
and (via subclass) Ollama's compatibility surface.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable

import httpx

from crucible.backends.base import (
    BackendError,
    ChatEvent,
    GenResult,
    ModelInfo,
    ToolCall,
    ToolsUnsupported,
)

_TOOL_ERROR_MARKERS = ("tool", "function")


class OpenAICompatBackend:
    name = "openai"

    def __init__(self, base_url: str, api_key: str = "", timeout_s: float = 300.0):
        # Normalize: accept base URLs with or without a trailing /v1.
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        self.base_url = base
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=10.0))

    def _headers(self) -> dict:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def close(self) -> None:
        await self._client.aclose()

    async def list_models(self) -> list[ModelInfo]:
        try:
            resp = await self._client.get(f"{self.base_url}/v1/models", headers=self._headers())
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(f"{self.name}: cannot list models: {e}") from e
        return [ModelInfo(id=m["id"]) for m in resp.json().get("data", [])]

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
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # Map the portable generation options; server-specific ones are dropped.
        options = options or {}
        if "seed" in options:
            payload["seed"] = options["seed"]
        if "temperature" in options:
            payload["temperature"] = options["temperature"]
        try:
            return await self._generate_once(payload, on_token)
        except _StreamOptionsRejected:
            payload.pop("stream_options")
            return await self._generate_once(payload, on_token)

    async def _generate_once(
        self, payload: dict, on_token: Callable[[str], None] | None
    ) -> GenResult:
        start = time.monotonic()
        t_first = t_last = None
        chunks: list[str] = []
        offsets: list[float] = []
        usage: dict | None = None
        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    if "stream_options" in payload and "stream_options" in body:
                        raise _StreamOptionsRejected
                    raise BackendError(f"{self.name}: HTTP {resp.status_code}: {body[:300]}")
                async for data in _sse_events(resp):
                    choices = data.get("choices") or []
                    if data.get("usage"):
                        usage = data["usage"]
                    if not choices:
                        continue
                    content = (choices[0].get("delta") or {}).get("content")
                    if content:
                        now = time.monotonic()
                        if t_first is None:
                            t_first = now
                        t_last = now
                        offsets.append(now - start)
                        chunks.append(content)
                        if on_token:
                            on_token(content)
        except httpx.HTTPError as e:
            raise BackendError(f"{self.name}: streaming failed: {e}") from e

        end = time.monotonic()
        t_first = t_first if t_first is not None else end
        t_last = t_last if t_last is not None else end
        approx = usage is None
        return GenResult(
            text="".join(chunks),
            ttft_s=t_first - start,
            prompt_tokens=(usage or {}).get("prompt_tokens", 0),
            gen_tokens=(usage or {}).get("completion_tokens", len(chunks)),
            gen_s=t_last - t_first,
            total_s=end - start,
            approx=approx,
            chunk_offsets_s=offsets,
        )

    async def server_version(self) -> str | None:
        return None

    async def model_info(self, model: str) -> dict:
        return {}

    async def chat_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        payload: dict = {"model": model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        # Accumulate tool-call fragments by index: id/name arrive first,
        # arguments concatenate across chunks.
        pending: dict[int, dict] = {}
        usage: dict | None = None
        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode(errors="replace")
                    if tools and any(m in body.lower() for m in _TOOL_ERROR_MARKERS):
                        raise ToolsUnsupported(body[:300])
                    raise BackendError(f"{self.name}: HTTP {resp.status_code}: {body[:300]}")
                async for data in _sse_events(resp):
                    if data.get("usage"):
                        usage = data["usage"]
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        yield ChatEvent(kind="text", text=delta["content"])
                    for frag in delta.get("tool_calls") or []:
                        entry = pending.setdefault(
                            frag.get("index", 0), {"id": "", "name": "", "arguments": ""}
                        )
                        if frag.get("id"):
                            entry["id"] = frag["id"]
                        fn = frag.get("function") or {}
                        if fn.get("name"):
                            entry["name"] = fn["name"]
                        if fn.get("arguments"):
                            entry["arguments"] += fn["arguments"]
        except httpx.HTTPError as e:
            raise BackendError(f"{self.name}: chat stream failed: {e}") from e

        for _, entry in sorted(pending.items()):
            yield ChatEvent(kind="tool_call", tool_call=_finalize_tool_call(entry))
        yield ChatEvent(kind="done", usage=usage)


class _StreamOptionsRejected(Exception):
    pass


def _finalize_tool_call(entry: dict) -> ToolCall:
    try:
        arguments = json.loads(entry["arguments"]) if entry["arguments"] else {}
    except json.JSONDecodeError:
        arguments = {"_raw": entry["arguments"]}
    if not isinstance(arguments, dict):
        arguments = {"_raw": entry["arguments"]}
    return ToolCall(id=entry["id"], name=entry["name"], arguments=arguments)


async def _sse_events(resp: httpx.Response) -> AsyncIterator[dict]:
    """Yield parsed JSON payloads from an SSE stream, stopping at [DONE]."""
    async for line in resp.aiter_lines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            return
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue
