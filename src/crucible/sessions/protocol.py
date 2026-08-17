"""The model↔sandbox command protocol: tool schema, fenced-block fallback parser,
system prompts, and output truncation for model feedback.
"""

from __future__ import annotations

import re

RUN_SHELL_TOOL = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": "Run a shell command inside your sandbox container and get "
        "its output. Commands run non-interactively under `sh -c`.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute.",
                }
            },
            "required": ["command"],
        },
    },
}

DONE_RE = re.compile(r"^\s*DONE:\s*(.*)$", re.MULTILINE)
_FENCE_RE = re.compile(r"```(?:sh|bash|shell)?\s*\n(.*?)```", re.DOTALL)

_COMMON = """\
You are operating inside CRUCIBLE, an LLM testing ground. You have shell access
to a disposable Linux container (image: {image}). Network access is {network}.
Commands run non-interactively — never invoke editors, pagers, or anything that
waits for input; use flags like -y and DEBIAN_FRONTEND=noninteractive. Each
command is killed after {timeout}s. Command output fed back to you may be
truncated in the middle for length.

Work step by step: run ONE command at a time, observe its output, then decide
the next command. When the task is complete (or impossible), reply with a line
starting with `DONE:` followed by a short summary of what you did — and stop
issuing commands."""

SYSTEM_NATIVE = _COMMON + """

Use the run_shell tool to execute commands. Call it with exactly one command at
a time."""

SYSTEM_FENCED = _COMMON + """

To execute a command, reply with exactly one fenced shell block:

```sh
your command here
```

Reply with at most ONE fenced block per message. The command's exit code and
output will be sent back to you in the next message."""


def system_prompt(mode: str, image: str, network: bool, timeout_s: int) -> str:
    template = SYSTEM_NATIVE if mode == "native" else SYSTEM_FENCED
    return template.format(
        image=image,
        network="ENABLED" if network else "DISABLED (no internet, no package installs)",
        timeout=timeout_s,
    )


def parse_fenced(text: str) -> str | None:
    """Extract the first fenced shell block, or None."""
    match = _FENCE_RE.search(text)
    if not match:
        return None
    command = match.group(1).strip()
    return command or None


def parse_done(text: str) -> str | None:
    """Return the DONE summary if the message signals completion."""
    match = DONE_RE.search(text)
    return match.group(1).strip() if match else None


def truncate_for_model(output: str, head: int = 4096, tail: int = 4096) -> str:
    """Squeeze huge command output before feeding it back to the model.

    The TUI still shows the full (capped) output; only the model's copy shrinks.
    """
    if len(output) <= head + tail:
        return output
    omitted = len(output) - head - tail
    return f"{output[:head]}\n[... {omitted} bytes truncated ...]\n{output[-tail:]}"


def format_exec_result(exit_code: int, stdout: str, stderr: str, truncated: bool) -> str:
    """Render an ExecResult as model feedback text."""
    parts = [f"exit code: {exit_code}" + (" (timeout)" if exit_code == 124 else "")]
    if stdout.strip():
        parts.append(f"stdout:\n{truncate_for_model(stdout)}")
    if stderr.strip():
        parts.append(f"stderr:\n{truncate_for_model(stderr)}")
    if truncated:
        parts.append("[note: output was capped before truncation]")
    if not stdout.strip() and not stderr.strip():
        parts.append("(no output)")
    return "\n".join(parts)
