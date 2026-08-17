"""Benchmark flow: hardware panel, model benchmark with live progress, report at the end."""

from __future__ import annotations

from rich.markdown import Markdown
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from crucible import hardware
from crucible.backends import BackendError
from crucible.benchmark import BenchmarkParams, BenchmarkRunner, get_prompt_set, save
from crucible.cli.common import pick_backend, pick_models
from crucible.cli.ui import confirm, console, rule, text
from crucible.config import Config
from crucible.report import render_benchmark_md


def hardware_table() -> Table:
    hw = hardware.probe()
    table = Table(title="Hardware", title_style="brand", border_style="grey30",
                  show_header=False, pad_edge=False)
    table.add_column(style="accent", no_wrap=True)
    table.add_column()
    table.add_row("OS", hw.os_name)
    table.add_row("CPU", f"{hw.cpu_model} — {hw.cores_physical}c/{hw.cores_logical}t "
                         f"@ {hw.freq_mhz:.0f} MHz, load {hw.load_pct:.0f}%")
    table.add_row("RAM", f"{hw.ram_used_gb:.1f} / {hw.ram_total_gb:.1f} GB used")
    table.add_row("Disk", f"{hw.disk_free_gb:.0f} GB free of {hw.disk_total_gb:.0f} GB")
    if not hw.gpus:
        table.add_row("GPU", "[muted]no NVIDIA GPU detected[/]")
    for i, gpu in enumerate(hw.gpus):
        table.add_row(
            f"GPU {i}",
            f"{gpu.name} — VRAM {gpu.vram_used_mb}/{gpu.vram_total_mb} MiB, "
            f"util {gpu.util_pct}%, {gpu.temp_c}°C",
        )
    return table


async def run(cfg: Config) -> None:
    rule("benchmark")
    console.print(hardware_table())
    console.print()

    backend_name = await pick_backend(cfg)
    if backend_name is None:
        return
    backend, models = await pick_models(cfg, backend_name, multi=True)
    try:
        if not models:
            return
        try:
            runs = max(1, int(await text("Runs per prompt?", str(cfg.benchmark.runs))))
            max_tokens = max(16, int(await text("Max tokens?", str(cfg.benchmark.max_tokens))))
        except ValueError:
            console.print("[err]numbers, please[/]")
            return
        params = BenchmarkParams(runs=runs, max_tokens=max_tokens,
                                 warmup=cfg.benchmark.warmup,
                                 prompt_set=cfg.benchmark.prompt_set)
        prompts = get_prompt_set(params.prompt_set)
        per_model = params.runs * len(prompts)
        console.print(
            f"[muted]{len(models)} model(s) × {per_model} generations, "
            f"max {params.max_tokens} tokens, warmup {'on' if params.warmup else 'off'}[/]"
        )
        if not await confirm("Fire?"):
            return

        report = None
        with Progress(
            SpinnerColumn(style="brand"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(complete_style="brand"),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        ) as progress:
            tasks = {m: progress.add_task(m, total=per_model, start=False) for m in models}

            def on_progress(ev: dict) -> None:
                task_id = tasks[ev["model"]]
                if ev["phase"] == "warmup":
                    progress.start_task(task_id)
                    progress.update(task_id, description=f"{ev['model']} [muted]warmup/load[/]")
                elif ev["phase"] == "model-done":
                    progress.update(task_id, description=f"{ev['model']} [ok]done[/]")
                elif ev["token"] is None:
                    progress.start_task(task_id)
                    progress.update(
                        task_id, completed=ev["done"],
                        description=f"{ev['model']} [muted]({ev['prompt_id']})[/]",
                    )

            runner = BenchmarkRunner(backend, models, prompts, params,
                                     hardware.probe(), on_progress)
            try:
                report = await runner.run()
            except BackendError as e:
                console.print(f"[err]benchmark failed: {e}[/]")
                return
    finally:
        await backend.close()

    # Every test ends with its report: render it into the terminal, keep the file.
    path = save(report)
    console.print()
    console.print(Markdown(render_benchmark_md(report)))
    console.print(f"\n[muted]report saved →[/] [accent]{path}[/]")
