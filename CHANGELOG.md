# Changelog

## 0.2.0 — 2026-08-23

- Determinism audit mode: same prompt × N, pinned seed + temperature 0 —
  verdict on whether the backend honors the seed.
- Tool-calling reliability suite: 12 scored cases × N trials (selection,
  typed argument filling, parallel calls, abstention), Wilson 95% CIs.
- Inspect screen: template & context X-ray — num_ctx footgun, tool/system
  template sections, double-BOS, template hash/overhead, live tool probe.
- Reports now carry model provenance (quant · size · family · ctx ·
  template sha) from /api/show.
- Harness: SearXNG search resource (captcha-free web search, autodetects a
  local searxng container).

- **Model replies render as Markdown**, in the TUI and inline alike. Everything
  CRUCIBLE writes itself — reports, transcripts, About — was already Markdown;
  the models' own output was the one place still shown raw, which is exactly
  where headings, lists and fenced code turn up. One-line answers stay on the
  speaker's line; anything with structure gets its own rendered block, with code
  syntax-highlighted in the terminal's own palette. Set
  `[ui] render_markdown = false` to see byte-for-byte what the model emitted —
  this is a testing ground, and sometimes that is the question. Model output is
  rendered, never interpreted: console markup in a reply is displayed, not
  applied.
- **CRUCIBLE can run its own vLLM.** vLLM fixes its model at launch and has no
  API to swap it, so switching models means replacing the server — which is now
  a spawn bar in Benchmark and Harness setup: type a model id, get a server on
  its own port under its own Docker label, and the model pickers repopulate from
  it. It never touches a vLLM you started yourself, and refuses to spawn when
  something else holds the GPUs, naming the process and container in the way.
  Configured under `[vllm_server]`; opt-in, with its own weight cache by default.
- **Release resources killswitch**: a red entry appears at the top of the home
  menu whenever CRUCIBLE is holding containers — sessions, harness resources and
  spawned servers alike — and frees them all after a confirmation. It is offered
  only when there is something to release, never moves the highlight onto itself,
  and touches nothing it did not start.
- Menu entries now lead with their shortcut key instead of a decorative icon:
  the keybinding is discoverable, the columns line up, and nothing depends on
  glyphs a terminal font may not have. Quit is no longer listed twice.
- Setup screens no longer stretch their rows to fill the panel, and primary
  buttons stay inside its border. The GPU line fits one row again, badge
  included, instead of wrapping.
- **Harness mode** (`⌘`, `h` on the home screen, `/attach` inline): chat with any
  model and spawn resources for it mid-conversation. Terminal resources give a
  shell in a disposable container; browser resources give real headless Chromium
  with internet, driven over CDP so navigation stays stateful across calls.
  Tools are namespaced per instance (`terminal_run`, `terminal2_run`,
  `browser_goto`, …) and the model is re-told what it has whenever a resource is
  attached or dropped. Works with nothing attached, and degrades to plain chat
  on a backend that rejects tools. Approve-each stops on acts (commands, clicks,
  navigation) but not on page reads; `max_tool_calls` bounds a runaway turn.
  Harness containers carry their own Docker label, so the Session purge and the
  harness never reach into each other.
- Machine monitor now names what holds GPU VRAM while a card is not idle —
  process, size and container name (`21.5G VLLM::Worker_TP0
  (kairos-vllm-active)`) — and stays silent, and cheap, while the cards are
  free. A card at 0% utilization still counts as busy when a model is resident.
- Benchmark modes: *standard* (`pp512`/`tg128`, llama-bench conventions and
  markdown table) and *context sweep* (`pp/tg @ dN` with prompt-cache
  verification), alongside the quick prompt suite.
- Latency distributions in every mode: TTFT/TPOT/ITL/E2EL with p50/p95/p99
  (NIM formulary, pooled ITL); server version recorded; metric definitions
  embedded in reports.
- Session terminal now echoes commands and exit codes; reasoning (`<think>`)
  blocks stripped from history and folded in chat; ASCII banner on results
  and in saved reports; home-screen machine monitor with GPU sparklines.

- Full-screen TUI is back as the default interface; the inline CLI remains via
  `crucible --inline` and as the automatic non-TTY fallback.
- Live machine monitor (CPU · RAM · per-GPU util/VRAM/temp) on the Benchmark
  and Session screens, and pinned under inline benchmark progress.
- Reports screen in the TUI: browse benchmark reports & session transcripts.
- Every config field now documents what it does and which mode uses it
  (⚖ benchmark · ⚗ session), in both the TUI form and the inline config menu.
- Session transcripts wired into the TUI; agent approval reworked to an async
  callback (fixes prompt crash inside a running event loop).

## 0.1.0 — 2026-08-17

Initial release.

- Full-screen Textual TUI: home banner, Benchmark, Sessions, Config, About.
- Benchmark: live hardware panel (CPU/RAM/disk/NVIDIA GPUs), model benchmarking
  with warmup, exact Ollama server-side metrics, OpenAI-compatible streaming
  metrics, JSON history with in-app browsing.
- Sessions: Docker-sandboxed agent loop with native tool-calling and automatic
  fenced-protocol fallback, approve-each/auto modes, live chat + terminal panes,
  stale-container purge.
- Config: TOML config for Ollama / vLLM / any OpenAI-compatible backend,
  benchmark params, sandbox limits; in-TUI editing plus $EDITOR escape hatch.
