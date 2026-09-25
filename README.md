# toomar

> A fast, dependency-free logging framework for Python — async by default,
> colored by default, and sustains **5x the throughput** of stdlib `logging`.

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](https://en.wikipedia.org/wiki/No_dependencies)

---

## Why toomar?

Python's `logging` module is battle-tested but synchronous: every `logger.info(...)`
call blocks until the message is formatted and written. For high-throughput
services — web servers, message brokers, data pipelines — that per-call overhead
adds up.

**toomar** is built from the ground up for throughput:

| feature | toomar | stdlib `logging` |
|---|---|---|
| dispatch | async worker thread | synchronous |
| writes | batched (up to 100 logs) | one `write` per record |
| color | automatic ANSI, zero deps | manual `Formatter` |
| throughput | **~554,000 logs/sec** | ~108,000 logs/sec |
| latency | ~1.8 µs / log | ~9.3 µs / log |
| dependencies | **0** | stdlib only |

```
toomar ██████████████████████████████ 553,902 logs/sec
stdlib ██████████ 107,721 logs/sec
→ toomar sustains 5.14x the throughput of stdlib
```

*Median of 7 runs × 500,000 log calls to an in-memory buffer. The first run
includes thread warmup, so the median is the honest headline — the async
worker amortises its cost as volume grows.*

![throughput — logs/sec (higher is better)](docs/bench_chart.png)

---

## Install

```bash
pip install toomar
```

Or, from source:

```bash
git clone https://github.com/sobhanzadehali/toomar.git
cd toomar
pip install -e .
```

---

## Quick start

```python
from toomar.logger import get_logger

log = get_logger("myapp")
log.info("server started", "on port", 8080)
log.warning("cache miss", "user=42")
log.error("connection refused", "host=db", "port=5432")
```

Output (auto-colored on capable terminals; levels shown in color):

```
server started on port 8080
cache miss user=42
connection refused host=db port=5432
```

---

## Configuration

### Levels

```python
from toomar.logger import get_logger

log = get_logger("app")
log.set_level("DEBUG")      # or INFO / WARNING / ERROR / CRITICAL

log.debug("verbose detail")  # shown
log.info("hello")            # shown
log.warning("careful")       # shown
```

### Sinks

toomar writes through **sinks**. Pluggable backends:

```python
from toomar.conf import Config
from toomar.sinks import ConsoleSink
from toomar.logger import get_logger

# write to stdout with color
cfg = Config(sinks=[ConsoleSink()], format=None)
log = get_logger("app", config=cfg)

# write to a file, no color, no autoflush
import sys
cfg = Config(
    sinks=[ConsoleSink(stream=open("app.log", "w"), colors=False, autoflush=False)],
    format=None,
)
```

### Custom format

```python
from toomar.conf import Config
from toomar.sinks import ConsoleSink
from toomar.logger import get_logger

cfg = Config(
    sinks=[ConsoleSink()],
    format="{asctime} {levelname} {name} {message}",
)
log = get_logger("app", config=cfg)
```

---

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Logger     │     │  _LogWorker  │     │   ConsoleSink│
│  (public API)│────▶│ (async queue)│────▶│  (formatter) │
│              │     │  batch=100   │     │              │
└──────────────┘     └──────────────┘     └──────────────┘
```

- `Logger` is the public face. Every `log.info(...)` builds a `Log` record and
  enqueues it — no formatting, no I/O on the caller's thread.
- `_LogWorker` runs a single daemon thread that drains the queue in batches of
  up to 100 records, so I/O syscalls are amortised.
- `BaseSink` is the backend interface. `ConsoleSink` renders colored output;
  file sinks, network sinks, or no-op sinks are easy to add.

---

## Benchmark

Run the live benchmark (uses [`rich`](https://github.com/Textualize/rich) for
the pretty table — degrades gracefully if absent):

```bash
BENCH_ITERS=1000000 PYTHONPATH=src python3 bench_logger.py
```

For a stable headline, run the median-of-N variant:

```bash
BENCH_RUNS=7 BENCH_ITERS=500000 PYTHONPATH=src python3 bench_stable.py
```
---

## License

[MIT](LICENSE)