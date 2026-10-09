"""Human-readable reports: Markdown benchmark reports and session transcripts.

Every benchmark run writes a .md report next to its .json data; every session
writes a streaming transcript. The Reports screen lists and renders them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from crucible.banner import BANNER, SUBTITLE
from crucible.benchmark.assessment import assess_result
from crucible.config import data_dir


def sessions_dir() -> Path:
    return data_dir() / "sessions"


# ---- benchmark reports ------------------------------------------------------


def render_benchmark_md(report: dict) -> str:
    hw = report.get("hardware", {})
    params = report.get("params", {})
    gpus = hw.get("gpus", [])
    gpu_line = (
        ", ".join(f"{g['name']} ({g['vram_mb']} MiB)" for g in gpus) if gpus else "none detected"
    )
    lines = [
        "```",
        BANNER,
        SUBTITLE.rjust(60),
        "```",
        "",
        "# CRUCIBLE benchmark report",
        "",
        f"- **When:** {report.get('timestamp', '?')}",
        f"- **Run:** {report.get('status', 'completion unknown (legacy report)')} · id {report.get('id', 'unavailable')}",
        f"- **Backend:** {report.get('backend', '?')} ({report.get('base_url', '')})",
        f"- **CPU:** {hw.get('cpu', '?')} · {hw.get('cores', '?')} threads"
        f" · {hw.get('ram_gb', '?')} GB RAM",
        f"- **GPU:** {gpu_line}",
        f"- **OS:** {hw.get('os', '?')}",
        f"- **Hardware scope:** {hw.get('scope', 'not recorded; do not assume this is the inference server')}",
        f"- **Params:** mode={params.get('mode', 'prompts')} · runs={params.get('runs', '?')}"
        f" · max_tokens={params.get('max_tokens', '?')}"
        f" · warmup={'on' if params.get('warmup') else 'off'}"
        f" · prompts={params.get('prompt_set', 'default')}",
        f"- **Standard/sweep:** reps={params.get('reps', '?')} · pp target={params.get('pp_tokens', '?')}"
        f" · output cap={params.get('tg_tokens', '?')} · seed={params.get('seed', '?')}"
        f" · depths={params.get('depths', [])}",
        f"- **Client:** Crucible {report.get('client', {}).get('version', 'unknown')}"
        f" · server {report.get('server', {}).get('version') or 'unknown'} · concurrency=1",
        "",
        "**How to read this:** lower first-text wait and latency are better; higher throughput "
        "is better for the same workload. Targets below are editable example chat goals, "
        "not an industry grade. Speed does not measure answer quality or serving capacity.",
        "",
    ]
    # Keep throughput stratified by workload rather than pooling pp and tg.
    test_rows = [(r["model"], t) for r in report.get("results", [])
                 for t in r.get("tests", [])
                 if t.get("skipped") or "tps_mean" in t]
    if test_rows:
        backend = report.get("backend", "?")
        lines.append("| model | backend | test | tok/s (mean ± σ) | reps | actual input / output tokens |")
        lines.append("|---|---|---|---|---|---|")
        for model, t in test_rows:
            if t.get("skipped"):
                lines.append(f"| {model} | {backend} | {t['test']} | *skipped: "
                             f"{t['skipped'][:60]}* | — | — |")
                continue
            approx = " ≈" if t.get("approx") else ""
            cache = (" (cache unverified)" if t.get("cache_hit", True) is None else
                     " (cache not reused)" if t.get("cache_hit", True) is False else "")
            ranges = ["–".join(str(v) for v in t.get(key, [])) or "unknown"
                      for key in ("prompt_tokens_range", "gen_tokens_range")]
            speed = (f"{t['tps_mean']:.2f} ± {t['tps_stdev']:.2f}{approx}{cache}"
                     if t.get("measured_reps", t.get("reps", 1)) else "unavailable")
            lines.append(
                f"| {model} | {backend} | {t['test']} | "
                f"{speed} | {t.get('reps', '?')} | {' / '.join(ranges)} |"
            )
        server = report.get("server", {})
        lines.append("")
        latency_rows = [(model, t) for model, t in test_rows if t.get("latency")]
        if latency_rows:
            lines.extend(["| model | test | first text p95 (s) | TPOT mean (ms) | content gap p99 (ms) |",
                          "|---|---|---|---|---|"])
            for model, test in latency_rows:
                latency = test["latency"]
                tpot = latency["tpot_ms"]
                gaps = latency["itl_ms"]
                tpot_text = f"{tpot['mean']:.2f}" if tpot["n"] else "—"
                gap_text = f"{gaps['p99']:.2f}" if gaps["n"] else "—"
                lines.append(f"| {model} | {test['test']} | {latency['ttft_s']['p95']:.3f} | "
                             f"{tpot_text} | {gap_text} |")
            lines.append("")
        lines.append(
            f"*server: {server.get('name', '?')} {server.get('version') or ''} · "
            f"warmup is excluded when enabled · ≈ = client throughput approximation; "
            f"TTFT/E2EL and content-chunk gaps always use client timing*"
        )
        lines.append("")

    for r in report.get("results", []):
        lines.append(f"## {r['model']}")
        lines.append("")
        assessment = assess_result(report, r)
        lines.append(f"**Evidence:** {assessment['samples']} completed requests · {assessment['evidence']}")
        lines.append("")
        if assessment["metrics"]:
            lines.extend(["| What you experience | Measured | Your target | Result |",
                          "|---|---|---|---|"])
            for metric in assessment["metrics"]:
                lines.append(f"| {metric['label']} | {metric['value']} | {metric['goal']} | {metric['status']} |")
            lines.append("")
            lines.extend(f"- {m['explanation']}" for m in assessment["metrics"])
            lines.append("")
        if assessment["warnings"]:
            lines.append("**Limits and follow-up:**")
            lines.append("")
            lines.extend(f"- {warning}" for warning in assessment["warnings"])
            lines.append("")
        prov = r.get("provenance")
        if prov:
            bits = []
            if prov.get("param_size"):
                bits.append(str(prov["param_size"]))
            if prov.get("quant"):
                bits.append(str(prov["quant"]))
            if prov.get("family"):
                bits.append(str(prov["family"]))
            if prov.get("model_context_length"):
                bits.append(f"ctx {prov['model_context_length']}")
            if prov.get("template_sha256"):
                bits.append(f"template sha {prov['template_sha256']}")
            lines.append(f"*{' · '.join(bits)}*")
            lines.append("")
        det = next((t for t in r.get("tests", []) if t.get("test") == "determinism"), None)
        if det:
            mark = "✓" if det.get("deterministic") else "✗"
            lines.append(f"**{mark} determinism:** {det.get('verdict', '?')}")
            if det.get("first_divergence_char") is not None:
                lines.append(f"  (outputs first diverge at character "
                             f"{det['first_divergence_char']})")
            lines.append("")
        if r.get("error"):
            lines.append(f"**ERROR:** {r['error']}")
            lines.append("")
            if not r.get("runs"):
                continue
        if not r.get("runs"):
            lines.extend(["No completed samples; metrics unavailable.", ""])
            continue
        if r.get("load_s") is not None:
            lines.append(f"Model load time: {r['load_s']:.1f} s")
            lines.append("")
        if not r.get("tests"):
            lines.append("| prompt | ttft s | prompt tok | prompt t/s | gen tok "
                         "| gen t/s | total s |")
            lines.append("|---|---|---|---|---|---|---|")
            for row in r.get("runs", []):
                approx = " ≈" if row.get("approx") else ""
                counts_known = row.get("token_count_source") != "chunks"
                pp = f"{row['prompt_tps']:.0f}" if counts_known else "—"
                tg = f"{row['gen_tps']:.1f}{approx}" if counts_known and row['gen_tps'] > 0 and not row.get('reasoning_observed') else "—"
                input_count = str(row["prompt_tokens"]) if counts_known else "unknown"
                output_count = str(row["gen_tokens"]) if counts_known else f"{row['gen_tokens']} chunks"
                lines.append(
                    f"| {row['prompt_id']} | {row['ttft_s']:.2f} | {input_count} "
                    f"| {pp} | {output_count} | {tg} "
                    f"| {row['total_s']:.2f} |"
                )
            lines.append("")
        s = r.get("summary", {})
        if params.get("mode") in ("std", "depth"):
            lines.extend(["Throughput and latency are shown per workload above; prefill and decode are not pooled into a single score.", ""])
            continue
        gen, ttft, ptps = s.get("gen_tps", {}), s.get("ttft_s", {}), s.get("prompt_tps", {})
        gen_text = f"{gen.get('mean', 0):.1f} t/s" if gen.get("n", 1) else "unavailable"
        pp_text = f"{ptps.get('mean', 0):.0f} t/s" if ptps.get("n", 1) else "unavailable"
        total_tokens = str(s.get("total_gen_tokens", 0)) if s.get("total_gen_tokens_known", True) else "unknown"
        lines.append(
            f"**Summary:** generation **{gen_text}** "
            f"(min {gen.get('min', 0):.1f} / max {gen.get('max', 0):.1f} "
            f"/ σ {gen.get('stdev', 0):.1f}) · "
            f"ttft {ttft.get('mean', 0):.2f} s · prompt {pp_text} · "
            f"{total_tokens} tokens"
            + (" · *≈ throughput; see timing/count provenance in JSON*" if s.get("approx") else "")
        )
        lines.append("")
        tpot, itl = s.get("tpot_ms", {}), s.get("itl_ms", {})
        e2el = s.get("e2el_s", {})
        if tpot.get("mean"):
            lines.append("| latency | ttft p50 | ttft p95 | tpot mean | chunk gap p50 "
                         "| chunk gap p95 | chunk gap p99 | e2el p95 |")
            lines.append("|---|---|---|---|---|---|---|---|")
            lines.append(
                f"| ms/s | {ttft.get('p50', 0) * 1000:.0f}ms | {ttft.get('p95', 0) * 1000:.0f}ms "
                f"| {tpot.get('mean', 0):.1f}ms | {itl.get('p50', 0):.1f}ms "
                f"| {itl.get('p95', 0):.1f}ms | {itl.get('p99', 0):.1f}ms "
                f"| {e2el.get('p95', 0):.2f}s |"
            )
            lines.append("")
    defs = report.get("metric_defs")
    if defs:
        lines.append(f"*metrics: tpot = {defs.get('tpot')} · itl = {defs.get('itl')}*")
        lines.append("")
    environment = report.get("environment")
    if environment:
        lines.extend(["## Benchmark environment", "",
                      "Client-host snapshots before/after only; they do not prove isolation during the run. "
                      "Remote inference server resources are not observed.", ""])
        for phase in ("before", "after"):
            snapshot = environment.get(phase, {})
            docker = snapshot.get("docker", {})
            lines.append(f"- **{phase}:** CPU {snapshot.get('cpu_load_pct', '?')}% · "
                         f"RAM {snapshot.get('ram_used_gb', '?')} GB · Docker {docker.get('status', 'unavailable')}")
            if docker.get("status") == "observed":
                names = ", ".join(f"{c['name']} ({c['state']})" for c in docker.get("containers", []))
                lines.append(f"  Containers: {names or 'none'}")
            for gpu in snapshot.get("gpus", []):
                lines.append(f"  GPU {gpu['name']}: {gpu['vram_used_mb']} MiB used · "
                             f"{gpu['util_pct']}% utilization · {gpu['temp_c']}°C")
        lines.append("")
    lines.append("---")
    lines.append("*Generated by CRUCIBLE — powered by ATHLAS.IO*")
    lines.append("")
    return "\n".join(lines)


def render_toolcalls_md(report: dict) -> str:
    server = report.get("server", {})
    params = report.get("params", {})
    lines = [
        "```",
        BANNER,
        SUBTITLE.rjust(60),
        "```",
        "",
        "# CRUCIBLE tool-calling report",
        "",
        f"- **When:** {report.get('timestamp', '?')}",
        f"- **Run:** {report.get('status', 'unknown (legacy report)')}",
        f"- **Backend:** {report.get('backend', '?')} "
        f"({server.get('version') or 'version unknown'})",
        f"- **Suite:** {params.get('cases', '?')} cases × {params.get('trials', '?')} trials "
        "— selection, argument filling, parallel calls, abstention. "
        "Nothing is executed; only the calls the model makes are scored.",
        "",
    ]
    for r in report.get("results", []):
        lines.append(f"## {r['model']}")
        lines.append("")
        if r.get("error"):
            lines.append(f"**ERROR:** {r['error']}")
            lines.append("")
            if not r.get("total"):
                continue
        if not r.get("total"):
            lines.extend(["No completed trials; pass rate unavailable.", ""])
            continue
        lo, hi = r.get("ci95", [0, 0])
        rate = r.get("pass_rate", 0.0)
        lines.append(
            f"**Pass rate: {r.get('passes', 0)}/{r.get('total', 0)} "
            f"({rate:.0%}) · 95% CI {lo:.0%}–{hi:.0%}**"
            + (f" · flaky: {', '.join(r['flaky_cases'])}" if r.get("flaky_cases") else "")
        )
        lines.append("")
        if r.get("status") == "incomplete" or r.get("total", 0) < r.get("expected_trials", 0):
            lines.extend([f"**Incomplete:** {r.get('total', 0)}/{r.get('expected_trials', '?')} trials completed. Untested trials are excluded.", ""])
        lines.append("| case | verifies | pass | failure notes |")
        lines.append("|---|---|---|---|")
        for c in r.get("cases", []):
            mark = "—" if not c["trials"] else "✓" if c["passes"] == c["trials"] else (
                "±" if c["passes"] else "✗")
            notes = "; ".join(c.get("fail_reasons", []))[:90]
            lines.append(f"| {c['case']} | {c['checks']} "
                         f"| {c['passes']}/{c['trials']} {mark} | {notes} |")
        lines.append("")
    lines.append("*Pass = correct tool, argument subset match (typed, case-insensitive "
                 "substrings for strings, 1% tolerance for numbers). CI = Wilson score "
                 "interval. Repeated trials of the same cases are correlated, so this interval "
                 "is descriptive and does not establish general tool-calling reliability. "
                 "This bundled suite is not an official BFCL score.*")
    lines.append("")
    lines.append("---")
    lines.append("*Generated by CRUCIBLE — powered by ATHLAS.IO*")
    lines.append("")
    return "\n".join(lines)


def save_benchmark_md(report: dict, json_path: Path) -> Path:
    md_path = json_path.with_suffix(".md")
    renderer = (render_toolcalls_md if report.get("kind") == "toolcalls"
                else render_benchmark_md)
    md_path.write_text(renderer(report), encoding="utf-8")
    return md_path


# ---- session transcripts ----------------------------------------------------


class SessionTranscript:
    """Streaming Markdown transcript of one sandbox session.

    Appends as events happen (flushed per write, crash-safe). Console output is
    kept in fenced blocks that are lazily closed when another entry arrives.
    """

    def __init__(self, path: Path, fh: TextIO):
        self.path = path
        self._fh = fh
        self._in_block = False

    @classmethod
    def create(cls, session_id: str, model: str, backend: str, image: str,
               network: bool) -> SessionTranscript:
        directory = sessions_dir()
        directory.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().astimezone()
        path = directory / f"{ts.strftime('%Y-%m-%dT%H-%M-%S')}_session_{session_id}.md"
        fh = path.open("a", encoding="utf-8")
        fh.write(
            f"# CRUCIBLE session transcript — {session_id}\n\n"
            f"- **Started:** {ts.isoformat(timespec='seconds')}\n"
            f"- **Model:** {model} @ {backend}\n"
            f"- **Sandbox:** {image} · network {'on' if network else 'off'}\n\n---\n"
        )
        fh.flush()
        return cls(path, fh)

    def _entry(self, text: str) -> None:
        if self._in_block:
            self._fh.write("```\n")
            self._in_block = False
        self._fh.write(text)
        self._fh.flush()

    def user(self, text: str) -> None:
        self._entry(f"\n**you ▸** {text}\n")

    def assistant(self, text: str) -> None:
        self._entry(f"\n**model ▸** {text}\n")

    def command(self, command: str) -> None:
        self._entry(f"\n```console\n$ {command}\n")
        self._in_block = True

    def output(self, stream: str, line: str) -> None:
        if not self._in_block:
            self._fh.write("\n```console\n")
            self._in_block = True
        prefix = "! " if stream == "stderr" else "" if stream == "stdout" else "· "
        self._fh.write(f"{prefix}{line}\n")
        self._fh.flush()

    def note(self, text: str) -> None:
        self._entry(f"\n*{text}*\n")

    def done(self, summary: str) -> None:
        self._entry(f"\n**✓ DONE:** {summary}\n")

    def close(self, reason: str = "session ended") -> None:
        if self._fh.closed:
            return
        self._entry(f"\n---\n*{reason} — {datetime.now().astimezone().isoformat(timespec='seconds')}*\n")
        self._fh.close()


# ---- report listing ---------------------------------------------------------


@dataclass
class ReportEntry:
    path: Path
    kind: str  # "benchmark" | "session"
    title: str
    mtime: float


def list_reports() -> list[ReportEntry]:
    """All Markdown reports, newest first."""
    from crucible.benchmark.store import benchmarks_dir

    entries: list[ReportEntry] = []
    for directory, kind in ((benchmarks_dir(), "benchmark"), (sessions_dir(), "session")):
        if not directory.is_dir():
            continue
        for path in directory.glob("*.md"):
            try:
                entries.append(
                    ReportEntry(path=path, kind=kind, title=path.stem,
                                mtime=path.stat().st_mtime)
                )
            except OSError:
                continue
    entries.sort(key=lambda e: e.mtime, reverse=True)
    return entries
