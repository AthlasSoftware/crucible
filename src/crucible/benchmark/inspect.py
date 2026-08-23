"""Template & context X-ray: surface the silent footguns that ruin models.

The community's loudest quiet failures: a missing num_ctx (Ollama silently
truncates at its small default), templates without tool/system sections,
double-BOS. None of these error — the model just gets worse. This module makes
them visible: model metadata, template hash and text, rule-based lints, and a
live probe (template token overhead + native-tool acceptance).
"""

from __future__ import annotations

from dataclasses import dataclass

from crucible.backends.base import BackendError, LLMBackend, ToolsUnsupported

_BOS_MARKERS = ("<s>", "<|begin_of_text|>", "<bos>", "<BOS>")
_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "probe_noop",
        "description": "Probe tool; never call it.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

# severity: "ok" | "warn" | "bad" | "info"


@dataclass
class Finding:
    severity: str
    title: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"severity": self.severity, "title": self.title, "detail": self.detail}


def lint_ollama(info: dict) -> list[Finding]:
    findings: list[Finding] = []
    template = info.get("template") or ""
    parameters = info.get("parameters") or ""
    model_ctx = info.get("model_context_length")

    if "num_ctx" in parameters:
        findings.append(Finding("ok", "num_ctx is set in the modelfile",
                                next((line.strip() for line in parameters.splitlines()
                                      if "num_ctx" in line), "")))
    else:
        detail = ("Ollama falls back to its own small default (2048–4096 depending on "
                  "version/env). Prompts beyond it are SILENTLY truncated from the front")
        if model_ctx:
            detail += f" — while the model itself supports {model_ctx} tokens"
        findings.append(Finding("bad", "no num_ctx in the modelfile", detail + "."))

    if not template:
        findings.append(Finding("warn", "no chat template exposed",
                                "cannot lint template structure"))
        return findings

    if ".Tools" in template or ".ToolCalls" in template:
        findings.append(Finding("ok", "template has a native tool section"))
    else:
        findings.append(Finding("warn", "template has NO tool section",
                                "native tool calling will be rejected or degrade to "
                                "prompt injection"))

    if ".System" in template:
        findings.append(Finding("ok", "template has a system-prompt slot"))
    else:
        findings.append(Finding("warn", "template has no system-prompt slot",
                                "system messages may be dropped or merged oddly"))

    stripped = template.lstrip()
    if any(stripped.startswith(m) for m in _BOS_MARKERS):
        findings.append(Finding(
            "warn", "template embeds a BOS token at the start",
            "if the backend also prepends BOS you get double-BOS — a documented "
            "quality-degrading bug class"))

    if ".Thinking" in template or "<think>" in template:
        findings.append(Finding("info", "reasoning template",
                                "model emits <think> blocks; CRUCIBLE strips them "
                                "from history in sessions"))

    if "stop" in parameters:
        stops = [line.strip() for line in parameters.splitlines() if "stop" in line]
        findings.append(Finding("info", f"{len(stops)} stop sequence(s) configured",
                                " · ".join(stops[:4])))
    return findings


async def probe_overhead(backend: LLMBackend, model: str) -> Finding | None:
    """Measure rendered-template token overhead for a one-word prompt."""
    try:
        r = await backend.generate_stream(model, "Hi", 1)
    except BackendError as e:
        return Finding("warn", "could not probe template overhead", str(e)[:120])
    if not r.prompt_tokens:
        return None
    return Finding("info", f"template overhead ≈ {max(0, r.prompt_tokens - 1)} tokens",
                   f"a 1-word prompt renders to {r.prompt_tokens} tokens all-in")


async def probe_tools(backend: LLMBackend, model: str) -> Finding:
    """Does the backend/model combination accept the tools parameter at all?"""
    messages = [{"role": "user", "content": "Say OK. Do not call any tool."}]
    try:
        stream = backend.chat_stream(model, messages, tools=[_PROBE_TOOL])
        async for _event in stream:
            pass
    except ToolsUnsupported:
        return Finding("bad", "native tool calling REJECTED by the backend",
                       "sessions/harness will fall back to the fenced protocol; "
                       "the tool-calling suite cannot run")
    except BackendError as e:
        return Finding("warn", "tool probe failed", str(e)[:120])
    return Finding("ok", "backend accepts native tool calling for this model")


async def inspect_model(backend: LLMBackend, model: str) -> dict:
    """Full X-ray: metadata + lints + live probes. Works best on Ollama;
    OpenAI-compatible servers expose no template, so only probes run there."""
    info = await backend.model_info(model)
    findings: list[Finding] = []
    if info:
        findings.extend(lint_ollama(info))
    else:
        findings.append(Finding(
            "info", "backend exposes no template/modelfile metadata",
            "openai-compatible APIs hide the template — verify it server-side; "
            "live probes below still apply"))
    overhead = await probe_overhead(backend, model)
    if overhead:
        findings.append(overhead)
    findings.append(await probe_tools(backend, model))
    return {
        "model": model,
        "backend": backend.name,
        "provenance": {
            k: v for k, v in (info or {}).items()
            if k in ("family", "quant", "param_size", "template_sha256",
                     "model_context_length") and v
        },
        "template": (info or {}).get("template", ""),
        "parameters": (info or {}).get("parameters", ""),
        "findings": [f.as_dict() for f in findings],
    }


_MARKS = {"ok": "✓", "warn": "⚠", "bad": "✗", "info": "·"}


def render_inspect_md(result: dict) -> str:
    from crucible.banner import BANNER, SUBTITLE

    prov = result.get("provenance", {})
    bits = [str(v) for k, v in prov.items() if k != "template_sha256"]
    lines = [
        "```", BANNER, SUBTITLE.rjust(60), "```", "",
        f"# CRUCIBLE model x-ray — {result['model']}",
        "",
        f"- **Backend:** {result.get('backend', '?')}",
        f"- **Model:** {' · '.join(bits) if bits else 'no metadata exposed'}",
    ]
    if prov.get("template_sha256"):
        lines.append(f"- **Template sha256:** {prov['template_sha256']}")
    lines += ["", "## Findings", ""]
    for f in result.get("findings", []):
        mark = _MARKS.get(f["severity"], "·")
        lines.append(f"- **{mark} {f['title']}**"
                     + (f" — {f['detail']}" if f.get("detail") else ""))
    if result.get("parameters"):
        lines += ["", "## Modelfile parameters", "", "```",
                  result["parameters"].strip(), "```"]
    if result.get("template"):
        lines += ["", "## Chat template", "", "```",
                  result["template"].strip(), "```"]
    lines += ["", "---", "*Generated by CRUCIBLE — powered by ATHLAS.IO*", ""]
    return "\n".join(lines)
