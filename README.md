# CRUCIBLE

```
 ██████╗██████╗ ██╗   ██╗ ██████╗██╗██████╗ ██╗     ███████╗
██╔════╝██╔══██╗██║   ██║██╔════╝██║██╔══██╗██║     ██╔════╝
██║     ██████╔╝██║   ██║██║     ██║██████╔╝██║     █████╗
██║     ██╔══██╗██║   ██║██║     ██║██╔══██╗██║     ██╔══╝
╚██████╗██║  ██║╚██████╔╝╚██████╗██║██████╔╝███████╗███████╗
 ╚═════╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝╚═╝╚═════╝ ╚══════╝╚══════╝
                                       POWERED BY ATHLAS.IO
```

A terminal app for testing local LLMs. Benchmark speed the way the community
measures it, check determinism and tool-calling reliability, scan a model for
silent misconfiguration, or give a model a sandboxed shell and a real browser
and see what it does with them.

The point is numbers you can trust: stddev or a confidence interval on every
measurement, warmup excluded, exact vs approximate always labeled, and full
provenance in every report so a result can be reproduced.

![CRUCIBLE home screen](docs/home.svg)

## Why

Local LLM setups fail quietly. A missing `num_ctx` makes Ollama truncate long
prompts without telling you. A broken chat template ruins tool calling with no
error. Some backends ignore your seed. Benchmark numbers get posted with no
error bars and no record of what was actually measured. CRUCIBLE exists to
make these things visible.

## Features

### Benchmark

Five modes:

| Mode | Question | Method |
|---|---|---|
| quick | How does this model feel? | 4 real prompts, N runs, exact server metrics |
| standard | What are my pp/tg numbers? | `pp512`/`tg128` following llama-bench conventions: synthetic deterministic prompts, per-test warmup, 5 reps, mean and stdev. The report includes a llama-bench-style markdown table you can post next to any llama.cpp result |
| context sweep | Is it still fast at 16k? | pp/tg at d4096/8192/16384 with the context primed into the KV cache. Prompt-cache hits are verified, misses are labeled as full prefill |
| determinism | Does my backend honor the seed? | Same prompt 5 times, pinned seed, temperature 0. Verdict plus where outputs first diverge |
| tool calling | Can this model actually call tools? | 12 bundled BFCL-style cases, 3 trials each: tool selection, typed argument filling, parallel calls, abstention. Pass rates with 95% Wilson intervals. Nothing is executed |

Every mode also reports latency distributions (TTFT, TPOT, ITL, E2EL with
p50/p95/p99, following the NVIDIA NIM definitions). ITL is pooled across runs
before percentiling, since mean tokens/s hides stutter and ITL p99 does not.
On Ollama the numbers come from exact server-side durations. On
OpenAI-compatible backends they are wall clock and marked with ≈.

Before you start a run, the MODEL FACTS line shows what matters for the
highlighted model: trained context length, configured `num_ctx` (or a warning
that Ollama will use its small default), KV-cache cost per 1k tokens, and an
estimate of how much context fits in your currently free VRAM.

Every run ends with a Markdown report, rendered in the terminal and saved to
disk next to the raw JSON. Reports carry model provenance (quant, size,
family, context length, template sha256), server version, and the metric
definitions used.

### Session

Spawns a disposable Docker container and runs an agent loop against it. The
model gets a `run_shell` tool (or a fenced-block protocol if the backend
rejects tools), proposes one command at a time, and each command lands in an
approval modal before it runs: the full command, then y / n / a. Resource
limits, network off by default, live terminal pane, Markdown transcript
written as it happens.

### Harness

A plain chat with a model, plus the ability to attach resources mid
conversation: a terminal (persistent shell container), a browser (headless
Chromium over CDP, so cookies and logins survive between calls), or search
(web search through a SearXNG instance, because search engines captcha
headless browsers on sight). Tools are namespaced per resource
(`terminal_run`, `browser_goto`, `search_web`), and a second terminal becomes
`terminal2`. Reads run without asking, actions stop for approval. With
nothing attached it is just a chat.

### Inspect

A scanner for the misconfigurations that never error: missing `num_ctx`,
template without a tool or system section, embedded double BOS, stop
sequences, template hash and token overhead, plus a live probe of whether the
backend accepts native tool calling for the model. Saves a report.

### Reports and Config

Every benchmark, session and harness is saved automatically. Browse, read,
open in `$EDITOR` or delete them from the Reports screen. Config is one TOML
file, and every setting explains itself in the UI: what it does and which
mode uses it.

## Install

You need [uv](https://docs.astral.sh/uv/) (or Python 3.12+), plus a running
[Ollama](https://ollama.com) or any OpenAI-compatible server
([vLLM](https://docs.vllm.ai), llama.cpp server, etc). Docker is only needed
for sessions and harness resources, not for benchmarking.

```sh
git clone https://github.com/AthlasSoftware/crucible
cd crucible
uv run crucible
```

First run creates `~/.config/crucible/config.toml` with sane defaults (Ollama
on `127.0.0.1:11434`, vLLM on `127.0.0.1:8000`). Reports land in
`~/.local/share/crucible/`.

`crucible` opens the full-screen TUI. `crucible --inline` runs a scriptable
inline CLI instead, and any non-TTY invocation (pipes, CI) falls back to it
automatically.

## Configuration

```toml
[backend]
active = "ollama"            # ollama | vllm | openai

[backend.ollama]
base_url = "http://127.0.0.1:11434"
default_model = "llama3.1:8b"

[backend.vllm]
base_url = "http://127.0.0.1:8000"

[backend.openai]             # any OpenAI-compatible endpoint
base_url = ""
api_key = ""

[benchmark]
runs = 3                     # quick mode: generations per prompt
max_tokens = 256
warmup = true                # untimed load run so cold start doesn't skew run 1

[sandbox]                    # sessions
image = "ubuntu:24.04"
cpus = 4.0
memory = "4g"
network = false              # turn on if the model should apt/pip install
approval = "approve-each"    # approve-each | auto
command_timeout_s = 60
max_iterations = 20

[harness]                    # resources you hand a model mid-chat
terminal_image = "ubuntu:24.04"
browser_image = "mcr.microsoft.com/playwright/python:v1.55.0-noble"
cpus = 4.0
memory = "4g"
terminal_network = false     # browsers always get network, terminals opt in
approval = "approve-each"    # stops on actions, not on page reads
command_timeout_s = 60
max_tool_calls = 12
searxng_url = ""             # empty = autodetect a *searxng* docker container

[vllm_server]                # a vLLM that CRUCIBLE can start itself (opt-in)
enabled = false
image = "vllm/vllm-openai:latest"
port = 8100
tensor_parallel_size = 1
gpu_memory_utilization = 0.90
max_model_len = 8192

[ui]
render_markdown = true       # false shows model replies byte for byte
```

## Methodology

- Exact where possible, labeled where not. Ollama's native API reports
  server-side `prompt_eval_duration` and `eval_duration`, with tokenization
  and network excluded, matching llama-bench. OpenAI-compatible backends give
  wall-clock timings and those numbers are marked ≈ wherever they appear.
- Warmup excluded. Model load time is reported separately, never mixed into
  throughput.
- Error bars on everything: stdev over reps for throughput, Wilson 95%
  intervals for pass rates, percentiles for latency.
- Provenance in every report, so a number can be reproduced or challenged.
- No cross-tool comparisons. API-side numbers include server overhead, and
  the reports say so instead of pretending otherwise.

## Notes

- Sessions and harness resources need the Docker socket (your user in the
  `docker` group). Containers are labeled (`crucible.session`,
  `crucible.harness`), released on exit, and a killswitch entry shows up on
  the home screen whenever CRUCIBLE is holding anything.
- With `network = false` the sandbox model cannot install packages. Turn it
  on in Config when the task needs it.
- Benchmarking a model bigger than your free VRAM spills to CPU and produces
  honest but slow numbers. MODEL FACTS and the live GPU monitor show the
  contention before you start.
- The harness search resource needs a SearXNG instance with its JSON API
  enabled (`settings.yml`, `search.formats: [html, json]`).
- CRUCIBLE binds no ports of its own. It is purely a client of your servers.

## Roadmap

Concurrency sweeps (aggregate vs per-stream tokens/s), VRAM high-water mark
and tokens per joule, instruction-following evals with confidence intervals,
needle-in-a-haystack context heatmaps, sandboxed code eval (pass@1) using the
existing Docker isolation, quant-ladder comparison, sustained-load thermal
soak.

## Contributing

Issues and PRs welcome, see [CONTRIBUTING.md](CONTRIBUTING.md). Short
version: `uv sync`, `uv run pytest`, `uv run ruff check`, and every measured
number needs its uncertainty stated.

## License

MIT, [ATHLAS.IO](https://athlas.io)
