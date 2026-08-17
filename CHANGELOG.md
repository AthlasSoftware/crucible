# Changelog

## Unreleased

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
