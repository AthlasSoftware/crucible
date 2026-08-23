"""Tool-calling reliability suite (BFCL-style, bundled and tiny).

Measures whether *your model + your quant + your backend* preserves tool
calling: correct tool selection, argument filling (typed subset matching, not
string comparison), parallel calls, and — crucially — abstention when no tool
fits. N trials per case; pass rates reported with a 95% Wilson interval, since
a bare percentage over a handful of trials reads more precise than it is.

Nothing is ever executed: the suite inspects the calls the model *makes*.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime

from crucible.backends.base import BackendError, LLMBackend, ToolCall, ToolsUnsupported
from crucible.hardware import HardwareInfo


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object", "properties": properties, "required": required,
            },
        },
    }


_WEATHER = _tool(
    "get_weather", "Get the current weather for a city.",
    {"city": {"type": "string", "description": "City name."}}, ["city"],
)
_ADD = _tool(
    "add_numbers", "Add two numbers and return the sum.",
    {"a": {"type": "number"}, "b": {"type": "number"}}, ["a", "b"],
)
_SEARCH = _tool(
    "web_search", "Search the web for current information.",
    {"query": {"type": "string"}}, ["query"],
)
_FILE_READ = _tool(
    "file_read", "Read a local file from disk.",
    {"path": {"type": "string"}}, ["path"],
)
_THERMOSTAT = _tool(
    "set_thermostat", "Set the room thermostat.",
    {
        "mode": {"type": "string", "enum": ["heat", "cool", "off"]},
        "temperature": {"type": "number", "description": "Target °C."},
    },
    ["mode"],
)
_TIMER = _tool(
    "set_timer", "Start a countdown timer.",
    {"minutes": {"type": "integer"}}, ["minutes"],
)
_EVENT = _tool(
    "create_event", "Create a calendar event.",
    {
        "title": {"type": "string"},
        "date": {"type": "string", "description": "ISO date YYYY-MM-DD."},
    },
    ["title", "date"],
)
_DELETE = _tool(
    "delete_file", "Delete a file from disk.",
    {"path": {"type": "string"}}, ["path"],
)
_SQL = _tool(
    "run_sql", "Run a read-only SQL query against the app database.",
    {"query": {"type": "string"}}, ["query"],
)
_CONVERT = _tool(
    "convert_units", "Convert a value between units.",
    {
        "value": {"type": "number"},
        "from_unit": {"type": "string"},
        "to_unit": {"type": "string"},
    },
    ["value", "from_unit", "to_unit"],
)


@dataclass
class ToolCase:
    """One scored scenario. expect is one of:
    {"call": {"name": ..., "args": {...}}}      — exactly one call, args subset
    {"no_call": True}                            — the model must NOT call
    {"parallel": [{"name":..., "args": {...}}]}  — all listed calls present
    """

    id: str
    tools: list[dict]
    prompt: str
    expect: dict
    checks: str = ""  # human description of what this case verifies


CASES: list[ToolCase] = [
    ToolCase(
        "select-basic", [_WEATHER, _SEARCH],
        "What's the weather like in Paris right now?",
        {"call": {"name": "get_weather", "args": {"city": "paris"}}},
        "picks the right tool and extracts the argument",
    ),
    ToolCase(
        "args-numbers", [_ADD, _WEATHER],
        "What is 847 plus 256? Use a tool.",
        {"call": {"name": "add_numbers", "args": {"a": 847, "b": 256}}},
        "numeric argument filling",
    ),
    ToolCase(
        "select-vs-distractor", [_SEARCH, _FILE_READ],
        "Find the latest news about the James Webb telescope.",
        {"call": {"name": "web_search", "args": {"query": "webb"}}},
        "chooses search over an irrelevant file tool",
    ),
    ToolCase(
        "abstain-chat", [_WEATHER, _ADD, _SEARCH],
        "Write a haiku about autumn leaves.",
        {"no_call": True},
        "abstains when the task needs no tool",
    ),
    ToolCase(
        "abstain-no-fit", [_WEATHER],
        "Translate the word 'hello' into French.",
        {"no_call": True},
        "abstains when no available tool fits",
    ),
    ToolCase(
        "parallel-two", [_WEATHER],
        "Compare the current weather in Oslo and in Madrid.",
        {"parallel": [
            {"name": "get_weather", "args": {"city": "oslo"}},
            {"name": "get_weather", "args": {"city": "madrid"}},
        ]},
        "issues two parallel calls for a comparison",
    ),
    ToolCase(
        "enum-arg", [_THERMOSTAT],
        "It's way too hot in here — cool the room down to 21 degrees.",
        {"call": {"name": "set_thermostat", "args": {"mode": "cool", "temperature": 21}}},
        "enum + number argument filling",
    ),
    ToolCase(
        "int-from-words", [_TIMER],
        "Set a timer for twelve minutes.",
        {"call": {"name": "set_timer", "args": {"minutes": 12}}},
        "converts a number word to a typed integer",
    ),
    ToolCase(
        "nested-fields", [_EVENT],
        "Book a meeting called Standup for 2026-09-01.",
        {"call": {"name": "create_event",
                  "args": {"title": "standup", "date": "2026-09-01"}}},
        "fills multiple required string fields",
    ),
    ToolCase(
        "path-arg", [_DELETE, _FILE_READ],
        "Please remove the file /tmp/old.log.",
        {"call": {"name": "delete_file", "args": {"path": "/tmp/old.log"}}},
        "destructive tool selected only when asked, path verbatim",
    ),
    ToolCase(
        "query-composition", [_SQL],
        "How many rows are in the users table? Query the database.",
        {"call": {"name": "run_sql", "args": {"query": "users"}}},
        "composes a query mentioning the right table",
    ),
    ToolCase(
        "unit-conversion", [_CONVERT, _ADD],
        "How many kilometers is 26.2 miles?",
        {"call": {"name": "convert_units",
                  "args": {"value": 26.2, "from_unit": "mile", "to_unit": "kilometer"}}},
        "multi-argument extraction with units",
    ),
]

_SYSTEM = (
    "You are a precise assistant with access to tools. Call a tool when — and "
    "only when — it is the right way to complete the request. If no tool fits, "
    "answer directly in text without calling anything."
)


def _value_matches(expected, actual) -> bool:
    if isinstance(expected, bool):
        return isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)):
        try:
            return math.isclose(float(actual), float(expected), rel_tol=0.01)
        except (TypeError, ValueError):
            return False
    if isinstance(expected, str):
        return expected.lower() in str(actual).lower()
    return expected == actual


def _call_matches(spec: dict, call: ToolCall) -> bool:
    if call.name != spec["name"]:
        return False
    return all(
        any(k.lower() == ak.lower() and _value_matches(v, av)
            for ak, av in call.arguments.items())
        for k, v in spec.get("args", {}).items()
    )


def score_case(case: ToolCase, calls: list[ToolCall]) -> tuple[bool, str]:
    """Returns (passed, short reason)."""
    if case.expect.get("no_call"):
        if calls:
            return False, f"called {calls[0].name} when it should have abstained"
        return True, "abstained"
    if "parallel" in case.expect:
        specs = case.expect["parallel"]
        unmatched = [s for s in specs
                     if not any(_call_matches(s, c) for c in calls)]
        if unmatched:
            return False, (f"missing {unmatched[0]['name']}({unmatched[0].get('args')}) "
                           f"— got {[c.name for c in calls] or 'no calls'}")
        return True, f"{len(specs)} parallel calls matched"
    spec = case.expect["call"]
    if not calls:
        return False, "no tool call made"
    if len(calls) > 1:
        return False, f"expected one call, got {len(calls)}"
    if not _call_matches(spec, calls[0]):
        return False, (f"got {calls[0].name}({calls[0].arguments}) — "
                       f"wanted {spec['name']}({spec.get('args')})")
    return True, "correct call"


def wilson_interval(passes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a pass rate."""
    if total == 0:
        return 0.0, 0.0
    p = passes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = (z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


class ToolCallRunner:
    """Runs the case suite × trials per model. progress_cb speaks the same dict
    protocol as BenchmarkRunner so the UI needs no special handling."""

    def __init__(self, backend: LLMBackend, models: list[str], trials: int,
                 hardware: HardwareInfo, progress_cb=None):
        self.backend = backend
        self.models = models
        self.trials = trials
        self.hardware = hardware
        self.progress_cb = progress_cb or (lambda _: None)
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    @property
    def total_per_model(self) -> int:
        return len(CASES) * self.trials

    async def _one_trial(self, model: str, case: ToolCase) -> list[ToolCall]:
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": case.prompt},
        ]
        calls: list[ToolCall] = []
        stream = self.backend.chat_stream(model, messages, tools=case.tools)
        async for event in stream:
            if event.kind == "tool_call" and event.tool_call:
                calls.append(event.tool_call)
        return calls

    async def run(self) -> dict:
        server_version = await self.backend.server_version()
        results = []
        for model in self.models:
            if self.cancelled:
                break
            results.append(await self._run_model(model))
        return self._report(results, server_version)

    async def _run_model(self, model: str) -> dict:
        done = 0
        case_rows = []
        error = None
        try:
            for case in CASES:
                if self.cancelled:
                    break
                passes = 0
                reasons: list[str] = []
                for _ in range(self.trials):
                    if self.cancelled:
                        break
                    calls = await self._one_trial(model, case)
                    ok, reason = score_case(case, calls)
                    passes += ok
                    if not ok:
                        reasons.append(reason)
                    done += 1
                    self.progress_cb({"model": model, "phase": "run", "done": done,
                                      "total": self.total_per_model,
                                      "prompt_id": case.id, "token": None})
                case_rows.append({
                    "case": case.id, "checks": case.checks,
                    "passes": passes, "trials": self.trials,
                    "fail_reasons": reasons[:3],
                })
        except ToolsUnsupported:
            error = ("backend rejected the tools parameter — this model/server "
                     "combination cannot do native tool calling")
        except BackendError as e:
            error = str(e)

        total = sum(c["trials"] for c in case_rows)
        passes = sum(c["passes"] for c in case_rows)
        lo, hi = wilson_interval(passes, total)
        flaky = [c["case"] for c in case_rows if 0 < c["passes"] < c["trials"]]
        return {
            "model": model,
            "cases": case_rows,
            "passes": passes,
            "total": total,
            "pass_rate": passes / total if total else 0.0,
            "ci95": [round(lo, 3), round(hi, 3)],
            "flaky_cases": flaky,
            **({"error": error} if error else {}),
        }

    def _report(self, results: list[dict], server_version: str | None) -> dict:
        hw = self.hardware
        return {
            "schema": 2,
            "kind": "toolcalls",
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "backend": self.backend.name,
            "base_url": getattr(self.backend, "base_url", ""),
            "server": {"name": self.backend.name, "version": server_version},
            "hardware": {
                "cpu": hw.cpu_model, "cores": hw.cores_logical,
                "ram_gb": round(hw.ram_total_gb, 1), "os": hw.os_name,
                "gpus": [{"name": g.name, "vram_mb": g.vram_total_mb} for g in hw.gpus],
            },
            "params": {"trials": self.trials, "cases": len(CASES)},
            "results": results,
        }
