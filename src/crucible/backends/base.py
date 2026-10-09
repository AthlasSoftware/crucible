"""Shared backend types and the LLMBackend protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol


class BackendError(Exception):
    """A backend request failed (connection, HTTP error, bad payload)."""


class ToolsUnsupported(BackendError):
    """The backend/model rejected the tools parameter; fall back to fenced mode."""


@dataclass
class ModelInfo:
    id: str
    size_bytes: int | None = None
    family: str | None = None
    quant: str | None = None


@dataclass
class GenResult:
    """One benchmark generation with timing. Durations in seconds.

    prompt_eval_s and load_s are exact on Ollama's native API and None on
    OpenAI-compatible backends. `approx` marks fallback/client throughput;
    token_count_source independently records whether usage is server reported.
    TTFT, E2EL and chunk offsets always use client timing.
    """

    text: str
    ttft_s: float
    prompt_tokens: int
    gen_tokens: int
    gen_s: float
    total_s: float
    prompt_eval_s: float | None = None
    load_s: float | None = None
    approx: bool = False
    # monotonic offsets (s) from request start for each received content chunk;
    # feeds TPOT/ITL/E2EL latency statistics
    chunk_offsets_s: list[float] = field(default_factory=list)
    # Token counts and timing have independent provenance. Client timings are
    # still approximate even when the server returns exact usage counts.
    timing_source: str = "server"
    token_count_source: str = "server"
    finish_reason: str | None = None
    cached_prompt_tokens: int | None = None
    reasoning_observed: bool = False

    @property
    def gen_tps(self) -> float:
        tokens = self.gen_tokens if self.timing_source == "server" else max(0, self.gen_tokens - 1)
        return tokens / self.gen_s if self.gen_s > 0 else 0.0

    @property
    def prompt_tps(self) -> float:
        # Exact when the server reported prompt eval time, else approximated
        # from wall-clock time to first token.
        denom = self.prompt_eval_s if self.prompt_eval_s else self.ttft_s
        tokens = self.prompt_tokens
        if self.prompt_eval_s is not None and self.cached_prompt_tokens is not None:
            tokens = max(0, tokens - self.cached_prompt_tokens)
        return tokens / denom if denom and denom > 0 else 0.0


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatEvent:
    """Streamed chat event: kind is "text", "tool_call" or "done"."""

    kind: str
    text: str = ""
    tool_call: ToolCall | None = None
    usage: dict | None = None


@dataclass
class ChatResponse:
    """Aggregate of one assistant turn, assembled by the agent loop."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMBackend(Protocol):
    name: str

    async def list_models(self) -> list[ModelInfo]: ...

    async def generate_stream(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        on_token: Callable[[str], None] | None = None,
        options: dict | None = None,
    ) -> GenResult: ...

    async def server_version(self) -> str | None: ...

    async def model_info(self, model: str) -> dict: ...

    def chat_stream(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[ChatEvent]: ...

    async def close(self) -> None: ...
