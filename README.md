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

**The LLM testing grounds.** A full-screen terminal app for putting local LLMs
through their paces: benchmark your hardware and models, or drop an LLM into a
sandboxed container with its own shell and watch it work — with a live machine
monitor (CPU · RAM · per-GPU util/VRAM/temp) pinned to the screen while tests
run. A scriptable inline mode (`crucible --inline`, automatic when piped)
covers automation and CI.

## Features

- **Benchmark** — three modes:
  - *quick* — four real prompts, a fast feel for a model;
  - *standard* — community-convention `pp512`/`tg128` throughput (mean ± stdev
    over 5 reps, warmup excluded, per-test warmup) with a **llama-bench-style
    markdown table** you can post next to any llama.cpp result;
  - *context sweep* — `pp/tg @ d4096/8192/16384`: does the model stay fast when
    the KV cache fills? Prompt-cache hits are verified; misses are honestly
    labeled as full-prefill numbers.

  Every mode also reports **latency distributions** — TTFT/TPOT/ITL/E2EL with
  p50/p95/p99 (NVIDIA NIM formulary, ITL pooled across runs) — plus server
  name/version and exact-vs-approximate labeling: exact server-side timings on
  Ollama, wall-clock (marked ≈) on OpenAI-compatible backends. **Every run ends
  with a Markdown report** rendered in your terminal and saved to disk (raw
  JSON next to it).
- **Sessions** — spawn a disposable Docker container and attach an LLM to it in
  an agentic loop. The model gets a `run_shell` tool (with automatic fallback to
  a fenced ```` ```sh ```` protocol for models without tool-calling); you
  approve each command or let it run on auto, with the model's stream and the
  sandbox terminal output inline in your scrollback. **Every session writes a
  Markdown transcript** as it happens.
- **Reports** — browse and read all past benchmark reports and session
  transcripts straight from the menu (or open them in `$EDITOR`).
- **Config** — TOML config with backend selection (**Ollama**, **vLLM**, or any
  OpenAI-compatible endpoint), model defaults, benchmark parameters, and sandbox
  limits. Quick-set the common fields from the menu or edit with your `$EDITOR`.
- **About** — because every good tool tells you what it is.

## Quickstart

Requirements: [uv](https://docs.astral.sh/uv/) (or Python ≥3.12 with pip),
Docker for sandbox sessions, and a running [Ollama](https://ollama.com) or
[vLLM](https://docs.vllm.ai) server.

```sh
# from a clone
uv run crucible

# once published to PyPI
uvx crucible-llm
```

First run creates `~/.config/crucible/config.toml` with sane defaults
(Ollama on `127.0.0.1:11434`, vLLM on `127.0.0.1:8000`). Point it elsewhere in
the Config menu or by editing the file. Reports land in
`~/.local/share/crucible/{benchmarks,sessions}/`.

`crucible` opens the full-screen TUI. `crucible --inline` runs the inline CLI
instead — and any non-TTY invocation (pipes, CI) falls back to it
automatically, with plain numbered prompts where empty input accepts the
default: `printf '5\n6\n' | crucible` prints the About page and quits.

Every setting is explained where you meet it: the Config screen describes each
field and marks which mode uses it (⚖ benchmark · ⚗ session), and the inline
config menu has a "what do the settings mean?" entry.

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
runs = 3
max_tokens = 256
warmup = true                # untimed warmup so cold model load doesn't skew run 1

[sandbox]
image = "ubuntu:24.04"
cpus = 4.0
memory = "4g"
network = false              # flip to true if the model should apt/pip install
approval = "approve-each"    # approve-each | auto
command_timeout_s = 60
max_iterations = 20
```

## Roadmap

- **What one machine can really do:** concurrency sweep (aggregate vs
  per-stream t/s — "how many agents can my rig feed"), VRAM high-water mark,
  power draw and tokens-per-joule via the GPU monitor.
- **Quality you can trust without a judge:** parametric IFEval-style
  instruction following with confidence intervals, synthetic
  needle-in-a-haystack context sweep (length × depth heatmap), determinism
  checks, sandboxed code eval (pass@1) using CRUCIBLE's Docker isolation.
- **Comparison:** quant-ladder mode (one model, every quant: size / speed /
  VRAM deltas), sustained-load thermal soak, shareable one-pager.

Principles: stddev/CI on every number, warmup excluded, exact vs approximate
always labeled, methodology embedded in every report, no cross-tool
leaderboard claims.

## Notes & caveats

- **Sandbox sessions need the Docker socket.** Your user must be able to run
  `docker` without sudo. If you ever containerize CRUCIBLE itself, you'd have to
  mount `/var/run/docker.sock` — understand the security implications first.
- Session containers are labeled `crucible.session=...`; CRUCIBLE cleans up its
  own on exit and offers a *purge stale sessions* action for leftovers.
- With `network = false` (the default) the model cannot install packages —
  intentional. Enable networking per config when the task needs it.
- Benchmarking a model larger than your free VRAM silently falls back to
  CPU/partial offload and reports honest-but-slow numbers; check the hardware
  tab's live VRAM readout for contention.
- CRUCIBLE binds no ports — it is purely a client of your LLM servers.

## License

MIT © [ATHLAS.IO](https://athlas.io)
