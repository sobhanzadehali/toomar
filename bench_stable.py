"""Run the benchmark N times and report median throughput + latency.

Run:  python3 bench_stable.py
"""

from __future__ import annotations

import io
import logging
import os
import statistics
import time

from toomar.conf import Config
from toomar.logger import get_logger as get_toomar_logger
from toomar.logger import shutdown as toomar_shutdown
from toomar.sinks import ConsoleSink

RUNS = int(os.environ.get("BENCH_RUNS", "7"))
ITERATIONS = int(os.environ.get("BENCH_ITERS", "500_000"))
MESSAGE = "hello world 42"


def _make_toomar_logger():
    buf = io.StringIO()
    cfg = Config(
        sinks=[ConsoleSink(stream=buf, colors=False, autoflush=False)],
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


def _time_it(fn, iterations: int) -> tuple[float, float, float]:
    """Return (elapsed_s, avg_s_per_log, logs_per_sec)."""
    start = time.perf_counter()
    for _ in range(iterations):
        fn()
    elapsed = time.perf_counter() - start
    avg = elapsed / iterations
    return elapsed, avg, iterations / elapsed


def main() -> None:
    toomar_lat: list[float] = []
    toomar_thr: list[float] = []
    stdlib_lat: list[float] = []
    stdlib_thr: list[float] = []

    for _ in range(RUNS):
        logger = _make_toomar_logger()
        for _ in range(1_000):
            logger.info(MESSAGE)
        _, lat, thr = _time_it(lambda: logger.info(MESSAGE), ITERATIONS)
        toomar_lat.append(lat)
        toomar_thr.append(thr)
        toomar_shutdown()

        stdlib_logger = _make_stdlib_logger()
        for _ in range(1_000):
            stdlib_logger.info(MESSAGE)
        _, lat, thr = _time_it(lambda: stdlib_logger.info(MESSAGE), ITERATIONS)
        stdlib_lat.append(lat)
        stdlib_thr.append(thr)

    t_lat = statistics.median(toomar_lat)
    t_thr = statistics.median(toomar_thr)
    s_lat = statistics.median(stdlib_lat)
    s_thr = statistics.median(stdlib_thr)

    print(f"toomar  latency: {t_lat * 1e6:6.2f} µs/log   throughput: {t_thr:,.0f} logs/sec")
    print(f"stdlib  latency: {s_lat * 1e6:6.2f} µs/log   throughput: {s_thr:,.0f} logs/sec")
    print(f"speedup: {s_lat / t_lat:.2f}x faster  ({s_thr / t_thr:.2f}x throughput)")


if __name__ == "__main__":
    main()