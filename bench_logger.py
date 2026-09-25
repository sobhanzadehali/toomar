#!/usr/bin/env python3
"""toomar vs stdlib logging — beautiful benchmark.

Run from the repo root:

    PYTHONPATH=src python3 bench_logger.py

What it measures
---------------
Per-log-call latency (µs) for two loggers writing to an in-memory buffer:

  * toomar.Logger   — async worker thread, batched writes (default)
  * logging.Logger  — synchronous StreamHandler (stdlib baseline)

Higher iterations = more realistic steady-state numbers; the async worker
amortises its cost across the queue, so the gap widens with volume.
"""

from __future__ import annotations

import io
import logging
import os
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from toomar.conf import Config
from toomar.logger import get_logger as get_toomar_logger
from toomar.logger import shutdown as toomar_shutdown
from toomar.sinks import BufferSink

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

ITERATIONS = int(os.environ.get("BENCH_ITERS", "1_000_000"))
WARMUP = int(os.environ.get("BENCH_WARMUP", "5_000"))

# fixed message so we measure formatting + dispatch, not allocation noise
MESSAGE = "hello world 42"


def _make_toomar_logger():
    cfg = Config(
        sinks=[BufferSink(colors=False)],
        format=None,
    )
    return get_toomar_logger("bench", config=cfg)


def _make_stdlib_logger():
    logger = logging.getLogger("bench")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _time_it(fn, iterations: int) -> tuple[float, float]:
    start = time.perf_counter()
    for _ in range(iterations):
        fn()
    elapsed = time.perf_counter() - start
    return elapsed, elapsed / iterations


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------

def run() -> None:
    console = Console()

    # ---- toomar ----------------------------------------------------------
    logger = _make_toomar_logger()
    for _ in range(WARMUP):
        logger.info(MESSAGE)

    t_total, t_avg = _time_it(lambda: logger.info(MESSAGE), ITERATIONS)
    toomar_shutdown()  # drain the worker queue

    # ---- stdlib ----------------------------------------------------------
    stdlib_logger = _make_stdlib_logger()
    for _ in range(WARMUP):
        stdlib_logger.info(MESSAGE)

    s_total, s_avg = _time_it(lambda: stdlib_logger.info(MESSAGE), ITERATIONS)

    # ---- render ----------------------------------------------------------
    t_tput = ITERATIONS / t_total
    s_tput = ITERATIONS / s_total

    table = Table(title="toomar vs Python stdlib logging", expand=False)
    table.add_column("logger", style="bold")
    table.add_column("mode", justify="right")
    table.add_column("total", justify="right")
    table.add_column("avg / log", justify="right")
    table.add_column("throughput", justify="right", style="bold green")

    table.add_row(
        Text("toomar", style="green"),
        Text("async", style="magenta"),
        f"{t_total:,.2f}s",
        f"{t_avg * 1e6:,.2f} µs",
        f"{t_tput:,.0f} / sec",
    )
    table.add_row(
        Text("stdlib", style="yellow"),
        Text("sync", style="red"),
        f"{s_total:,.2f}s",
        f"{s_avg * 1e6:,.2f} µs",
        f"{s_tput:,.0f} / sec",
    )

    console.print()
    console.print(
        Panel(
            f"iterations: {ITERATIONS:,}   warmup: {WARMUP:,}   "
            f"message: {MESSAGE!r}",
            title="benchmark config",
            border_style="dim",
        )
    )
    console.print(table)
    console.print()

    # throughput comparison: two bars, longer = faster (the intuitive reading)
    # both bars share the same scale so the length difference is the story
    bar_len = 30
    t_bar = "█" * max(1, int(bar_len * t_tput / max(t_tput, s_tput)))
    s_bar = "█" * max(1, int(bar_len * s_tput / max(t_tput, s_tput)))
    console.print(
        f"[bold green]toomar[/bold green] {t_bar} {t_tput:,.0f} logs/sec"
    )
    console.print(
        f"[bold yellow]stdlib[/bold yellow] {s_bar} {s_tput:,.0f} logs/sec"
    )
    console.print(
        f"[bold]→[/bold] toomar sustains "
        f"[bold green]{t_tput / s_tput:.2f}x[/bold green] "
        f"the throughput of stdlib"
    )
    console.print()


if __name__ == "__main__":
    run()