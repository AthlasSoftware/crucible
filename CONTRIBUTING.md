# Contributing to CRUCIBLE

Thanks for wanting to help. This is a small, tight codebase on purpose —
please keep it that way.

## Setup

```sh
git clone https://github.com/AthlasSoftware/crucible
cd crucible
uv sync
uv run crucible        # run the app
uv run pytest          # tests (offline-safe; no backends required)
uv run ruff check src tests
```

Python ≥ 3.12, managed with [uv](https://docs.astral.sh/uv/). Runtime
dependencies are deliberately few (`textual`, `rich`, `questionary`, `httpx`,
`psutil`, `tomli-w`) — a PR that adds one needs a good reason.

## Layout

```
src/crucible/
  backends/    Ollama + OpenAI-compatible clients (exact metrics vs labeled ≈)
  benchmark/   engine (modes), synthetic prompts, tool-calling suite,
               inspect (X-ray lints), result store
  sessions/    Docker sandbox, agent loop, command protocol
  harness/     chat with attachable resources (terminal, browser, search)
  servers/     vLLM servers CRUCIBLE spawns itself
  ui/          Textual screens; theme.tcss is the design system
  cli/         the inline (non-TTY / --inline) interface
```

Engine code never imports UI code. New benchmark types go in `benchmark/`
with pure, unit-testable scoring/aggregation functions.

## Ground rules

- **Honest numbers.** Every measured value ships with its uncertainty
  (stdev, percentile, or confidence interval), warmup excluded, and exact vs
  approximate labeled. No cross-tool leaderboard claims.
- **Silent failure is the enemy.** If something degrades (cache miss, seed
  ignored, template footgun), say so in the report rather than averaging it
  away.
- **Tests:** pure logic gets unit tests; screens get a mount/navigation smoke
  test. The suite must pass with no backends and no Docker available.
- **Style:** `ruff` clean; match the surrounding code; comments explain
  constraints, not narration.

## Reporting issues

Include your backend and version (`ollama --version` / server image tag), the
model id and quant, and — for benchmark disputes — the saved report JSON from
`~/.local/share/crucible/benchmarks/`. That file carries the provenance
needed to reproduce.
