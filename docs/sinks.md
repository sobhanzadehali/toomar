# Sinks

A sink is where logs go. toomar ships four, all built on the same two-method
interface, and you can add your own in about ten lines.

- [The interface](#the-interface)
- [`ConsoleSink`](#consolesink) — stdout only
- [`BufferSink`](#buffersink) — in memory
- [`FileSink`](#filesink) — one rotating file
- [`LevelFileSink`](#levelfilesink) — one rotating file per level
- [Rotation](#rotation)
- [Archiving](#archiving)
- [Format strings](#format-strings)
- [Lifecycle and `close()`](#lifecycle-and-close)
- [Writing your own sink](#writing-your-own-sink)
- [Reusing the machinery directly](#reusing-the-machinery-directly)
- [Known limitations](#known-limitations)

---

## The interface

```python
class BaseSink(ABC):
    @abstractmethod
    def flush(self, logs: list[Log]) -> None: ...
    def close(self) -> None: ...   # optional, defaults to a no-op
```

- `flush` is always called on the worker thread with a **non-empty** batch of up
  to 100 `Log` records. It should do the whole batch in as few syscalls as
  possible — that is the entire point of batching.
- `close` is called by `shutdown()`, which is itself registered as an `atexit`
  hook. Override it to stop threads, close file handles, flush buffers.

A sink is attached through a `Config`:

```python
from toomar import Config, ConsoleSink, get_logger

cfg = Config(sinks=[ConsoleSink()], format=None)
log = get_logger("app", config=cfg)
```

Every sink in the list receives every batch, so ordering and routing are the
sink's business, not the logger's.

---

## `ConsoleSink`

```python
ConsoleSink(*, autoflush=True, terminator="\n", colors=None, color_map=None)
```

Writes batches to **stdout and nothing else**. It never opens, rotates or
archives a file — for disk output use `FileSink` or `LevelFileSink`.

| parameter | default | meaning |
|---|---|---|
| `autoflush` | `True` | flush stdout after each batch. `False` drops a syscall per batch when stdout is a file or pipe |
| `terminator` | `"\n"` | appended after the last line of a batch |
| `colors` | `None` | `None` auto-detects, `True`/`False` force |
| `color_map` | `None` | level → color name, replacing `DEFAULT_COLORS` |

`sys.stdout` is resolved on **every** flush rather than captured at
construction, so `contextlib.redirect_stdout`, pytest's capture, notebooks and
embedded interpreters all behave. There is deliberately no `stream=` parameter:
a sink that writes somewhere other than stdout is a different kind of sink.

Colour is automatic. Escape sequences are emitted only when stdout is an
interactive terminal that understands them, so piping to a file or another
process stays clean. `NO_COLOR` and `TERM=dumb` are respected.

The default palette is DEBUG gray, INFO green, WARNING yellow, ERROR red,
CRITICAL bright magenta. Override it with any name from `toomar.colors`
(`fg`, `fg256`, `rgb`, `style`):

```python
ConsoleSink(colors=True, color_map={"ERROR": "bright_red", "INFO": "cyan"})
```

The whole batch is rendered and handed to the stream in a single `write` call.

---

## `BufferSink`

```python
BufferSink(*, colors=False, terminator="\n", color_map=None)
```

Collects output in memory. This is what to use where you would have reached for
`ConsoleSink(stream=StringIO())` — no file descriptor, no syscalls, and a
clean API instead of a stream you have to pass in.

| method | meaning |
|---|---|
| `getvalue()` | everything buffered so far, as one `str` |
| `clear()` | drop the buffer; it stays reusable |
| `len(sink)` | number of characters buffered |

Colour is off by default, because a buffer is almost never a terminal.

```python
from toomar import BufferSink, Config, get_logger, shutdown

sink = BufferSink()
log = get_logger("app", config=Config(sinks=[sink], format=None))
log.info("hello")

shutdown()          # drain the worker thread first
sink.getvalue()     # 'hello\n'
sink.clear()
```

Access is guarded by a lock: the worker thread writes while your thread reads.
Writes are asynchronous, so call `shutdown()` (or wait) before reading if you
need to be sure the batch has landed.

---

## `FileSink`

```python
FileSink(
    path,
    *,
    max_bytes=10 * 1024 * 1024,
    backup_count=3,
    interval=None,
    at=None,
    archive_after=None,
    archive_check=600.0,
    format="{asctime} {levelname} {name} {message}",
    encoding="utf-8",
    terminator="\n",
)
```

Writes every batch to one file, with rotation and archiving. The file handle is
opened **lazily on the first write** and parent directories are created then,
so constructing a sink never touches the filesystem.

Each batch is formatted, handed to the file as one payload, and flushed once.

```python
FileSink(
    "logs/app.log",
    max_bytes=10 * 1024 * 1024,   # roll at 10 MB
    interval="1d",                # and at midnight; exclusive with at=
    backup_count=5,
    archive_after=7 * 86400,      # zip generations older than 7 days
    archive_check=3600.0,         # check hourly
)
```

Extra methods: `rotate()` rolls now, `sweep()` runs one archive sweep
synchronously and returns how many files it archived, `path` and
`rotating_file` expose the writer, `archiver` is the background archiver or
`None`.

---

## `LevelFileSink`

```python
LevelFileSink(
    directory,
    files,
    *,
    max_bytes=10 * 1024 * 1024,
    backup_count=3,
    interval=None,
    at=None,
    archive_after=None,
    archive_check=600.0,
    format="{asctime} {levelname} {name} {message}",
    encoding="utf-8",
    terminator="\n",
)
```

Routes each record to its own file, chosen by **exact level match**:

```python
LevelFileSink(
    "logs",
    files={"ERROR": "error.log", "INFO": "info.log"},
    max_bytes=5 * 1024 * 1024,
    backup_count=3,
    archive_after=30 * 86400,
)
```

`ERROR` records go to `logs/error.log`, `INFO` records to `logs/info.log`.
Relative names resolve under `directory`; absolute paths are used as given. Two
levels may not target the same file — that raises `ValueError` at construction,
because it is always a configuration mistake.

**Matching is exact, and that is deliberate.** A level absent from `files` is
dropped, not folded into a neighbour:

| level | `files={"ERROR": ..., "INFO": ...}` |
|---|---|
| `ERROR` | `error.log` |
| `INFO` | `info.log` |
| `DEBUG` | dropped |
| `WARNING` | dropped |
| `CRITICAL` | dropped |

So a `WARNING` never lands in your error file unless you list it. Add
`"WARNING": "warning.log"` or `{"WARNING": "error.log", "ERROR": "error.log"}`
— the latter is the one case where two levels sharing a file is what you want,
and since it is explicit rather than inferred, the duplicate check will
complain. In that situation use two `FileSink`s instead, or route both levels
in one sink by subclassing.

A batch is walked **once**: lines accumulate per writer, then each writer gets
a single `write`. One `write` syscall per file per batch, and no line is
formatted twice.

Extra methods: `rotate()` rolls every file, `rotate("ERROR")` rolls one,
`sweep()` runs one archive sweep, `files` reports the resolved level → path
mapping. One archiver thread covers every file this sink owns.

---

## Rotation

Rotation is on **size, time, or both**.

### Size

`max_bytes` is checked on every write: if the live file already holds bytes and
the incoming payload would push it past the limit, the file rolls first. A
payload that is on its own larger than `max_bytes` is still written — it would
otherwise roll forever and never land. `max_bytes=None` disables size rotation.

### Generations and `backup_count`

```
app.log        ← live, always the newest data
app.log.1      ← previous generation
app.log.2      ← older still
```

A roll closes the handle, renames `app.log` → `app.log.1`, shifts
`app.log.N-1` → `app.log.N`, and deletes whatever falls off the end. Renames
use `os.replace`, so no data is ever copied.

| `backup_count` | behaviour |
|---|---|
| `3` (default) | keep 3 generations, delete the 4th oldest |
| `None` | keep every generation |
| `0` | overwrite the live file on every roll, keep nothing |

Rotating a file that does not exist is a no-op, not an error.

### Time

Two mutually exclusive options — passing both raises `ValueError`:

- `interval=` — relative. Accepts a bare number of seconds, a suffixed token,
  or a sum of tokens: `3600`, `"90"`, `"45s"`, `"30m"`, `"1h"`, `"2d"`, `"1w"`,
  `"1h30m"`, `"1d2h3m4s"`.
- `at=` — wall clock. `"HH:MM"`, `"HH:MM:SS"` or a `datetime.time`. `"00:00"`
  rolls at midnight local time.

The deadline is cached and recomputed after each roll, so the cost is one clock
call per batch. A wall-clock boundary is converted to the monotonic scale
internally, which keeps `at=` correct across DST changes and NTP steps.

### Choosing

| want | use |
|---|---|
| cap file size, keep a few | `max_bytes=10_000_000, backup_count=5` |
| one file per day | `interval="1d"` or `at="00:00"` |
| cap size *and* roll daily | both — whichever comes first |
| never delete anything | `backup_count=None, archive_after=None` |

---

## Archiving

Set `archive_after` to a number of seconds and a daemon thread starts. Every
`archive_check` seconds it takes the generations older than `archive_after`,
groups them by the day their **file mtime** falls on, and writes them into
`<stem>-<YYYY-MM-DD>.zip` in the same directory before deleting the originals.

```
logs/app.log                  ← live, never archived
logs/app.log.1
logs/app.log.2
logs/app-2026-09-24.zip       ← app.log.1 and app.log.2, as they were
```

The bundle is named after the day the data landed on disk, not the day the
sweep happened to run. Bundles are opened in append mode and entries already in
the `namelist()` are skipped, so re-sweeping never duplicates them. Entry names
are the generation index: `app.log.1`, `app.log.2`.

The **live file is never archived** — only numbered generations are.

`Archiver.sweep()` is public and synchronous, so you can drive it yourself and
skip the thread entirely:

```python
sink = LevelFileSink("logs", {"ERROR": "error.log"}, archive_after=86400)
sink.sweep()          # returns how many generations it archived
sink.close()          # stops the thread
```

### `backup_count` and `archive_after` interact

`backup_count` deletes old generations. If `archive_after` is set, the archiver
should already have taken anything that old — but only if it ran. Pick a
`backup_count` comfortably larger than the number of rotations you expect within
one `archive_after` window, and set `archive_check` well under `archive_after`,
or the oldest generation can be deleted before the archiver ever sees it.

The safest configuration is `backup_count=None` (keep everything) with
`archive_after` set: nothing is discarded until it has been zipped.

### Known archive caveat

Archive entry names are the generation index. If index `1` is reused on a
different day, the new file is discarded without being added, because the entry
name already exists in that bundle. In practice this needs a long-lived process
that rolls, ages out, rolls again into the same index, and ages out again on
the same day as the first archive. If you need exactness there, use a
`backup_count` that prevents index reuse, or archive by day from a cron job
rather than in-process.

---

## Format strings

File sinks format each record with a `str.format` template over four fields:

| field | value |
|---|---|
| `{asctime}` | the record's `created_date`, `%Y-%m-%d %H:%M:%S` by default |
| `{levelname}` | `"INFO"`, `"ERROR"`, ... |
| `{name}` | the logger name |
| `{message}` | the message, undecorated |

- `{asctime:PATTERN}` overrides the timestamp, e.g. `{asctime:%H:%M:%S}`.
- The other fields take the usual `format()` spec, e.g. `{levelname:<8}`.
- `format=None` writes the bare message.
- Literal text, `{{` and `}}` work as normal.

Templates are compiled once per distinct string and cached, so the hot path is a
list walk and a `join`, not a regex scan per record. An unknown field name,
malformed braces, an unsupported conversion or an invalid `{asctime}` pattern
raises `ValueError` when the sink is **constructed** — a bad config fails
loudly at startup rather than silently dropping fields on the first write.

```python
FileSink("logs/app.log", format="{asctime:%H:%M:%S} {levelname:<8} {name} {message}")
FileSink("logs/app.log", format="{levelname}: {message}")
FileSink("logs/app.log", format=None)   # just the message
```

`Config(format=...)` is separate and currently unused; per-sink `format=` is
what applies.

---

## Lifecycle and `close()`

```python
from toomar import shutdown

shutdown()   # drain the worker, then close every sink
```

`shutdown()` is idempotent and is registered as an `atexit` hook at import, so
buffered logs reach their sinks and open files are closed even if the program
ends without calling it. It drains the queue before closing, so records
enqueued a moment earlier are not lost.

For a file sink, `close()` stops the archiver thread first, then closes the
file handle. Calling it twice is a no-op. **Writing after `close()` raises
`RuntimeError`** — the worker catches sink exceptions and warns on stderr
rather than letting them kill the logging thread, so a closed sink produces a
warning and a silently dropped record, not a crash.

A sink whose `flush` or `close` raises produces a warning on stderr and is
skipped; the other sinks still run.

```python
class MySink(BaseSink):
    def close(self) -> None:
        ...
```

---

## Writing your own sink

Subclass `BaseSink` and implement `flush`. Do the whole batch in as few
operations as you can — that is where batching pays off.

```python
import json
from pathlib import Path

from toomar import BaseSink, Log


class JsonLinesSink(BaseSink):
    """one JSON object per log line, in a directory of daily files."""

    def __init__(self, directory: str) -> None:
        self._directory = Path(directory)
        self._handle = None
        self._day = None

    def flush(self, logs: list[Log]) -> None:
        day = logs[-1].created_date.date().isoformat()
        if day != self._day:
            self._rotate(day)
        payload = "".join(
            json.dumps(
                {
                    "ts": log.created_date.isoformat(),
                    "level": log.level,
                    "logger": log.logger_name,
                    "message": log.message,
                }
            )
            + "\n"
            for log in logs
        )
        self._handle.write(payload)
        self._handle.flush()

    def _rotate(self, day: str) -> None:
        if self._handle is not None:
            self._handle.close()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._handle = (self._directory / f"{day}.jsonl").open(
            "a", encoding="utf-8"
        )
        self._day = day

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
```

```python
cfg = Config(sinks=[JsonLinesSink("logs")], format=None)
log = get_logger("app", config=cfg)
```

Note what the example does and does not do: it opens files lazily, rotates on a
boundary rather than on every batch, and implements `close()` so `shutdown()`
tidies up. It uses no toomar internals — `BaseSink` and `Log` are the whole
contract.

---

## Reusing the machinery directly

`toomar.rotation` has no toomar imports and is usable on its own.

```python
from toomar.rotation import RotatingFile, Archiver, parse_interval

parse_interval("1h30m")   # 5400.0

writer = RotatingFile("out/data.log", max_bytes=1_000_000, interval="1h")
writer.write("a line\n")
writer.flush()
writer.rotate()
writer.generations()               # [<Path('out/data.log.1')>]

archiver = Archiver([writer], after=86400, check_interval=600)
archiver.start()
archiver.sweep()                   # synchronous, returns files archived
archiver.close()
```

Useful members:

| member | meaning |
|---|---|
| `RotatingFile.write(payload)` | roll if due, then append. raises `RuntimeError` after `close()` |
| `RotatingFile.flush()` | push buffered bytes to the OS |
| `RotatingFile.rotate()` | roll now |
| `RotatingFile.generations()` | numbered generations, newest first |
| `RotatingFile.aged(now, after)` / `.aged_entries(...)` | generations past an age, with mtimes |
| `RotatingFile.discard(path)` | delete a generation |
| `RotatingFile.path` / `.size` / `.closed` / `.terminator` | introspection |
| `Archiver.sweep()` | archive everything aged, return the count |
| `Archiver.start()` / `.close()` | manage the daemon thread |
| `parse_interval(value)` | `"1h30m"` → `5400.0` |
| `parse_time_of_day(value)` | `"00:00"` → `datetime.time` |
| `next_roll_deadline(interval=..., at=...)` | the deadline, in `clock()` units |

`RotatingFile` also takes `clock=` and `wall_clock=` callables, which is how
the time rotation tests run instantly and deterministically:

```python
now = [0.0]
writer = RotatingFile("out.log", interval="1h", clock=lambda: now[0])
writer.write("a\n")
now[0] += 3601        # the deadline has passed
writer.write("b\n")   # rolls here
```

Every mutation goes through a per-instance lock, which the archiver also takes
when it inspects generations, so a roll can never rename a file out from under
a sweep in progress.

---

## Known limitations

- **The worker is a process-wide singleton.** It is created from the first
  `Config` that reaches `get_logger`, so a later `Config` with different sinks
  is ignored. Build the config you want before the first `get_logger()` call,
  or call `shutdown()` first to rebuild it.
- **Level routing is exact**, so unlisted levels are dropped. See
  [`LevelFileSink`](#levelfilesink) for how to route them elsewhere.
- **`Config.format` is unused**; per-sink `format=` applies.
- **The console sink does not apply a format.** It prints the bare message,
  colored by level, with no timestamp.
- **Archive entry names are generation indices**, which can collide on reuse.
  See [Known archive caveat](#known-archive-caveat).
- **Not provided:** non-file sinks (syslog, network), compression of the live
  file, and `logging`-style threshold routing.
