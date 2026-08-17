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

- **Benchmark** — hardware panel (CPU, RAM, disk, per-GPU VRAM/utilization via
  `nvidia-smi`) and a model benchmark suite measuring time-to-first-token,
  prompt tokens/s and generation tokens/s. Ollama runs report *exact*
  server-side metrics; OpenAI-compatible backends report streamed usage counts.
  **Every run ends with a Markdown report** rendered in your terminal and saved
  to disk (with raw JSON next to it) for later comparison.
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
