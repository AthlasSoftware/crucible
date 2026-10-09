# Making Crucible a benchmark people can trust

Review: 2026-10-09. This is a product and methodology assessment, not a claim
of MLPerf compliance or an external certification.

Crucible is useful today as an interactive diagnostic for a model/backend
combination. Becoming a reference benchmark also requires fixed workloads,
quality requirements, controlled load, reproducibility and independent
verification. A readable score alone cannot establish those properties.

## What this pass addresses

- Shared TUI/Markdown interpretation against explicit goals: first-text p95
  ≤ 1 second, mean decode ≥ 20 tokens/second and p95 content-chunk gap
  ≤ 100 milliseconds by default. These are editable
  **example chat goals**, not universal definitions of good hardware. Other
  applications need other goals. A fast incorrect answer remains incorrect.
- Completion state, sample counts, descriptive-tail warnings, actual input and
  output token ranges, separate client/server timing and count provenance.
- Retained measurements after failure; cancellation no longer looks complete.
  Tool trials count only completed attempts, and parallel tool scoring rejects
  unexpected extra calls. Repeated cases are explicitly described as correlated.
- E2EL ends at the last content chunk, excluding delayed usage/done messages.
  Client decode divides tokens after the first by the first-to-last interval.
  Unknown token counts cannot produce a comparative token-throughput grade.
- Ollama prefill subtracts explicitly reported cached tokens. Cache reuse at
  context depth is unknown when the backend supplies no cached-token count.
- Warmup controls apply to standard tests, including prefill/decode warmups.
  Configured repetitions and output caps now actually control those tests.
- Host/container snapshots before and after performance runs, plus explicit
  container controls. Snapshots are evidence of those moments, not a guarantee
  that the machine stayed isolated during the measured interval.

## Remaining priorities

| Priority | Gap found in the code | Why it matters | Acceptance criterion |
|---|---|---|---|
| P0 | Synthetic length is a word target; exact tokenization and reasoning modes are not controlled | `pp512` can represent different work across tokenizers, templates and servers; hidden thinking can distort visible-text metrics | Versioned workload manifests; exact tokens or explicit length buckets; separate visible/reasoning counts; detection of truncation and early stopping |
| P0 | Only serial requests; no arrival-rate or concurrency sweep | Single-request decode speed does not measure service capacity | Concurrency/load sweeps reporting per-user latency, total output tok/s, requests/s, failures and goodput under declared SLOs |
| P0 | No answer-quality gate for speed tests | Throughput can improve by producing shorter, wrong or incomplete answers | Versioned quality datasets and scoring; minimum quality requirements before claiming an improvement |
| P0 | Small default samples and repeated tool cases | p95/p99 and Wilson intervals can look stronger than the evidence supports | Repeated independent runs, uncertainty per workload, explicit sample plans and confidence requirements; clustered analysis for tool cases |
| P0 | Serving configuration is only partially captured | A model name does not identify weights, runtime or tuning | Model revision/digest, tokenizer/template revisions, quantization, server image digest, launch flags, sampling and cache settings; replayable manifest |
| P1 | Baseline comparison is absent | A standalone number cannot establish a regression or an improvement | Compare matching workloads/metric definitions, show relative changes and uncertainty, explicitly identify configuration differences |
| P1 | Resource telemetry is before/after on the client host | A competing process, thermal throttling or remote server contention can go unseen | Opt-in sampled telemetry on the actual server, overhead accounting, driver/power/clock/temperature traces and contention annotations |
| P1 | TUI/inline feature parity and unattended execution are incomplete | Menu input is fragile for reproducible CI | Named CLI commands and manifests, deterministic seeds where supported, cancellation-safe artifact writes and documented exit codes |
| P1 | No independent conformance/reference suite | Cross-tool claims need independent verification | Timing fixtures, backend contract tests, a versioned schema and validator, reproducible reference runs and comparison against a reference client |
| P2 | Distribution, governance and external review are early | An industry standard is also an adoption and trust problem | Published protocol/version policy, transparent limitations, independent reproductions and review of proposed methodology changes |

Recommended order: finish the P0 measurement contract, add one repeatable CI
profile, then build baseline comparison and load sweeps on that contract.
Avoid introducing a combined “Crucible score”: speed, quality, reliability and
capacity answer different questions and should remain independently readable.

## Preparing a host from Crucible

Open **Resources** from Home (`e`) or Benchmark setup. It lists Docker state,
CPU/RAM, ownership and attributed local GPU compute allocations. Missing GPU
attribution is shown as `—`, not as proof that a container uses no GPU memory.

Select one competing container and choose the appropriate action:

1. **Pause / Resume** suspends/resumes processes. Allocated memory remains;
   pause is not a GPU-memory release operation or proof that the device is idle.
2. **Stop / Start** terminates/restarts a service. Stop allows 10 seconds for
   shutdown before force is used; in-memory work is lost and models must reload.
   Refresh GPU readings before running the test. Start explicitly afterwards.

Each action requires confirmation, defaults to Cancel, and rechecks the exact
container ID, current state and protections. Active resources owned by this
Crucible process and containers publishing configured loopback endpoint ports
are protected. `--rm` containers cannot be stopped here because Docker would
delete them. Resume a paused container before stopping it.

Nothing is automatically stopped or restarted. Closing Resources does not
restore services; Docker retains the chosen state. The Docker CLI may target a
remote daemon, while GPU readings are local. Port protection cannot identify
every proxy or host-network endpoint. Containers are discovered without
collecting environment variables or arbitrary labels into reports.

## External references used in the review

- [NVIDIA metric definitions](https://docs.nvidia.com/nim/benchmarking/llm/2.0.0/metrics):
  request-to-first-token and last-content timing, distinctions between per-user
  and total throughput, and the need to align definitions before comparing tools.
- [MLPerf Endpoints](https://mlcommons.org/benchmarks/endpoints/) and
  [MLPerf Inference rules](https://github.com/mlcommons/inference_policies/blob/master/inference_rules.adoc):
  load-dependent performance, reproducibility, quality requirements and review.
  These inform the gap analysis; Crucible does not implement their protocol.
- [Docker pause](https://docs.docker.com/reference/cli/docker/container/pause/) and
  [Docker stop](https://docs.docker.com/reference/cli/docker/container/stop/):
  process suspension versus termination and graceful-stop semantics.
- [Ollama usage fields](https://github.com/ollama/ollama/blob/main/docs/api/usage.mdx):
  prompt evaluation duration covers uncached tokens, and cached-token counts
  are a separate field. Older servers may omit that field.
