"""Backend factory: config → concrete LLMBackend."""

from __future__ import annotations

from crucible.backends.base import (
    BackendError,
    ChatEvent,
    ChatResponse,
    GenResult,
    LLMBackend,
    ModelInfo,
    ToolCall,
    ToolsUnsupported,
)
from crucible.backends.ollama import OllamaBackend
from crucible.backends.openai_compat import OpenAICompatBackend

__all__ = [
    "BackendError",
    "ChatEvent",
    "ChatResponse",
    "GenResult",
    "LLMBackend",
    "ModelInfo",
    "OllamaBackend",
    "OpenAICompatBackend",
    "ToolCall",
    "ToolsUnsupported",
    "get_backend",
]


def get_backend(config, name: str | None = None) -> LLMBackend:
    """Build a backend from a Config; `name` overrides the configured active one."""
    name = name or config.backend.active
    endpoint = config.backend.endpoint(name)
    if name == "ollama":
        return OllamaBackend(endpoint.base_url or "http://127.0.0.1:11434")
    backend = OpenAICompatBackend(endpoint.base_url, api_key=endpoint.api_key)
    backend.name = name
    return backend
