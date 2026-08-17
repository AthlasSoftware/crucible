"""Shared CLI flows: backend/model picking."""

from __future__ import annotations

from crucible.backends import BackendError, LLMBackend, ModelInfo, get_backend
from crucible.cli.ui import checkbox, console, select
from crucible.config import BACKEND_NAMES, Config


def pick_backend(cfg: Config) -> str | None:
    return select(
        "Backend?",
        [(name, name) for name in BACKEND_NAMES],
        default=cfg.backend.active,
    )


async def fetch_models(backend: LLMBackend) -> list[ModelInfo] | None:
    try:
        with console.status(f"[muted]listing models on {backend.name}…[/]", spinner="dots"):
            return await backend.list_models()
    except BackendError as e:
        console.print(f"[err]{e}[/]")
        return None


def _label(m: ModelInfo) -> str:
    if m.size_bytes:
        return f"{m.id}  ({m.size_bytes / 1e9:.1f} GB)"
    return m.id


async def pick_models(cfg: Config, backend_name: str, multi: bool) -> tuple[LLMBackend, list[str]]:
    """Returns (backend, models); models is empty on cancel. Caller closes backend."""
    backend = get_backend(cfg, backend_name)
    models = await fetch_models(backend)
    if not models:
        return backend, []
    default = cfg.backend.endpoint(backend_name).default_model
    choices = [(_label(m), m.id) for m in models]
    if len(models) == 1:
        console.print(f"[muted]single model on {backend_name}: {models[0].id}[/]")
        return backend, [models[0].id]
    if multi:
        preselected = {default} if any(m.id == default for m in models) else {models[0].id}
        picked = checkbox("Models to benchmark?", choices, preselected)
    else:
        chosen = select("Model?", choices, default=default if default else models[0].id)
        picked = [chosen] if chosen else []
    return backend, picked
