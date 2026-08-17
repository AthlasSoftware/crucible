# Changelog

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
