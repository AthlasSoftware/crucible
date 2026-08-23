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
        f"- **Backend:** {report.get('backend', '?')} ({report.get('base_url', '')})",
        f"- **CPU:** {hw.get('cpu', '?')} · {hw.get('cores', '?')} threads"
        f" · {hw.get('ram_gb', '?')} GB RAM",
        f"- **GPU:** {gpu_line}",
        f"- **OS:** {hw.get('os', '?')}",
        f"- **Params:** runs={params.get('runs', '?')}"
        f" · max_tokens={params.get('max_tokens', '?')}"
        f" · warmup={'on' if params.get('warmup') else 'off'}"
        f" · prompts={params.get('prompt_set', 'default')}",
        "",
    ]
    # Standardized tests (std/depth modes): the llama-bench-style table first —
    # copy-pastable next to any llama-bench result. Only throughput tests belong
    # here; verdict-style tests (determinism) render in their model sections.
    test_rows = [(r["model"], t) for r in report.get("results", [])
                 for t in r.get("tests", [])
                 if t.get("skipped") or "tps_mean" in t]
    if test_rows:
        backend = report.get("backend", "?")
        lines.append("| model | backend | test | t/s |")
        lines.append("|---|---|---|---|")
        for model, t in test_rows:
            if t.get("skipped"):
                lines.append(f"| {model} | {backend} | {t['test']} | *skipped: "
                             f"{t['skipped'][:60]}* |")
                continue
            approx = " ≈" if t.get("approx") else ""
            cache = "" if t.get("cache_hit", True) else " (full prefill)"
            lines.append(
                f"| {model} | {backend} | {t['test']} | "
                f"{t['tps_mean']:.2f} ± {t['tps_stdev']:.2f}{approx}{cache} |"
            )
        server = report.get("server", {})
        lines.append("")
        lines.append(
            f"*server: {server.get('name', '?')} {server.get('version') or ''} · "
            f"reps exclude warmup · ≈ = wall-clock approximation (API side); "
            f"exact server timings on ollama*"
        )
        lines.append("")

    for r in report.get("results", []):
        lines.append(f"## {r['model']}")
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
                lines.append(
                    f"| {row['prompt_id']} | {row['ttft_s']:.2f} | {row['prompt_tokens']} "
                    f"| {row['prompt_tps']:.0f} | {row['gen_tokens']} | {row['gen_tps']:.1f}{approx} "
                    f"| {row['total_s']:.2f} |"
                )
            lines.append("")
        s = r.get("summary", {})
        gen, ttft, ptps = s.get("gen_tps", {}), s.get("ttft_s", {}), s.get("prompt_tps", {})
        lines.append(
            f"**Summary:** generation **{gen.get('mean', 0):.1f} t/s** "
            f"(min {gen.get('min', 0):.1f} / max {gen.get('max', 0):.1f} "
            f"/ σ {gen.get('stdev', 0):.1f}) · "
            f"ttft {ttft.get('mean', 0):.2f} s · prompt {ptps.get('mean', 0):.0f} t/s · "
            f"{s.get('total_gen_tokens', 0)} tokens"
            + (" · *approximate counts*" if s.get("approx") else "")
        )
        lines.append("")
        tpot, itl = s.get("tpot_ms", {}), s.get("itl_ms", {})
        e2el = s.get("e2el_s", {})
        if tpot.get("mean"):
            lines.append("| latency | ttft p50 | ttft p95 | tpot mean | itl p50 "
                         "| itl p95 | itl p99 | e2el p95 |")
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
            continue
        lo, hi = r.get("ci95", [0, 0])
        rate = r.get("pass_rate", 0.0)
        lines.append(
            f"**Pass rate: {r.get('passes', 0)}/{r.get('total', 0)} "
            f"({rate:.0%}) · 95% CI {lo:.0%}–{hi:.0%}**"
            + (f" · flaky: {', '.join(r['flaky_cases'])}" if r.get("flaky_cases") else "")
        )
        lines.append("")
        lines.append("| case | verifies | pass | failure notes |")
        lines.append("|---|---|---|---|")
        for c in r.get("cases", []):
            mark = "✓" if c["passes"] == c["trials"] else (
                "±" if c["passes"] else "✗")
            notes = "; ".join(c.get("fail_reasons", []))[:90]
            lines.append(f"| {c['case']} | {c['checks']} "
                         f"| {c['passes']}/{c['trials']} {mark} | {notes} |")
        lines.append("")
    lines.append("*Pass = correct tool, argument subset match (typed, case-insensitive "
                 "substrings for strings, 1% tolerance for numbers). CI = Wilson score "
                 "interval — small samples deserve honest error bars.*")
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
