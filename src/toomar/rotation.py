"""
size and time based log rotation, plus a daemon thread that zips aged
generations.

this module is deliberately free of any toomar imports, so it is reusable on its
own: any component that wants "append text, roll over at N bytes or every hour,
and zip what got old" can use :class:`RotatingFile` and :class:`Archiver`
without going through a sink.

layout produced by rotation::

    app.log        <- live file, always the newest data
    app.log.1      <- previous generation
    app.log.2      <- older still
    app-2026-09-24.zip   <- archived generations, one bundle per stream per day
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta
from datetime import time as time_of_day
from pathlib import Path
from typing import Callable, Iterable, Sequence

__all__ = [
    "Archiver",
    "RotatingFile",
    "next_roll_deadline",
    "parse_interval",
    "parse_time_of_day",
]

#: default size ceiling before a roll, 10 MiB
DEFAULT_MAX_BYTES = 10 * 1024 * 1024

#: default number of rotated generations kept on disk
DEFAULT_BACKUP_COUNT = 3

_INTERVAL_UNITS: dict[str, float] = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}

_INTERVAL_TOKEN = re.compile(r"(\d+(?:\.\d+)?)\s*([smhdw]?)", re.IGNORECASE)

_TIME_OF_DAY = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


def parse_interval(value: str | float | int) -> float:
    """turn a human interval into seconds.

    accepts a bare number of seconds, a suffixed token, or a sum of tokens::

        parse_interval(90)        # 90.0
        parse_interval("90")      # 90.0
        parse_interval("30m")     # 1800.0
        parse_interval("1h")      # 3600.0
        parse_interval("1d")      # 86400.0
        parse_interval("1h30m")   # 5400.0

    units are ``s`` seconds, ``m`` minutes, ``h`` hours, ``d`` days, ``w`` weeks.
    a bare number is seconds. the result must be greater than zero.
    """
    if isinstance(value, bool):
        raise TypeError("interval must be a string or a number, not bool")
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds <= 0:
            raise ValueError(f"interval must be positive, got {value!r}")
        return seconds
    if not isinstance(value, str):
        raise TypeError(
            f"interval must be a string or a number, got {type(value).__name__}"
        )

    text = value.strip()
    if not text:
        raise ValueError("interval must not be empty")

    total = 0.0
    position = 0
    for match in _INTERVAL_TOKEN.finditer(text):
        if match.start() != position:
            raise ValueError(f"invalid interval {value!r}")
        position = match.end()
        unit = match.group(2).lower() or "s"
        total += float(match.group(1)) * _INTERVAL_UNITS[unit]

    if position != len(text) or total <= 0:
        raise ValueError(f"invalid interval {value!r}")
    return total


def parse_time_of_day(value: str | time_of_day) -> time_of_day:
    """normalise a wall clock rotation boundary.

    accepts ``datetime.time`` or ``"HH:MM"`` / ``"HH:MM:SS"``.
    """
    if isinstance(value, time_of_day):
        return value
    if not isinstance(value, str):
        raise TypeError(
            f"at= must be a string or datetime.time, got {type(value).__name__}"
        )

    match = _TIME_OF_DAY.match(value.strip())
    if match is None:
        raise ValueError(f"invalid time of day {value!r}, expected 'HH:MM' or 'HH:MM:SS'")

    hour, minute, second = (int(part) if part else 0 for part in match.groups())
    try:
        return time_of_day(hour, minute, second)
    except ValueError as exc:
        raise ValueError(f"invalid time of day {value!r}: {exc}") from exc


def next_roll_deadline(
    interval: str | float | int | None = None,
    at: str | time_of_day | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> float | None:
    """compute the next time based roll deadline, in ``clock()`` units.

    ``interval`` is relative to now: ``clock() + interval``. ``at`` is a wall
    clock boundary, converted into the monotonic scale so the caller only ever
    compares against ``clock()``. ``None`` for both means "no time rotation".

    ``interval`` and ``at`` are mutually exclusive.
    """
    if interval is not None and at is not None:
        raise ValueError("interval= and at= are mutually exclusive; pass only one")

    if interval is not None:
        return clock() + parse_interval(interval)

    if at is not None:
        boundary = parse_time_of_day(at)
        now_wall = wall_clock()
        moment = datetime.fromtimestamp(now_wall)
        candidate = moment.replace(
            hour=boundary.hour,
            minute=boundary.minute,
            second=boundary.second,
            microsecond=0,
        )
        if candidate <= moment:
            candidate += timedelta(days=1)
        return clock() + max(0.0, candidate.timestamp() - now_wall)

    return None


def _generation_index(path: Path, base: Path) -> int | None:
    """``app.log.3`` -> ``3``; returns ``None`` for anything else."""
    prefix = base.name + "."
    name = path.name
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


class RotatingFile:
    """an append only text file that rolls over on size, on time, or both.

    ``write(payload)`` is the only thing callers need. it checks the cached time
    deadline (one ``clock()`` call per batch), rotates if the payload would push
    the live file past ``max_bytes``, then writes and adds the **encoded** byte
    length to the running size — ``len(str)`` undercounts anything non ASCII.

    the file is opened lazily on the first write and its parent directories are
    created then, so constructing a sink never touches the filesystem.

    every mutation goes through a per instance lock, which is also what the
    archiver takes when it inspects generations, so a roll can never rename a
    file out from under a sweep in progress.
    """

    __slots__ = (
        "_at",
        "_backup_count",
        "_clock",
        "_closed",
        "_deadline",
        "_encoding",
        "_handle",
        "_interval",
        "_lock",
        "_max_bytes",
        "_path",
        "_size",
        "_terminator",
        "_wall_clock",
    )

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_bytes: int | None = DEFAULT_MAX_BYTES,
        backup_count: int | None = DEFAULT_BACKUP_COUNT,
        interval: str | float | int | None = None,
        at: str | time_of_day | None = None,
        encoding: str = "utf-8",
        terminator: str = "\n",
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if interval is not None and at is not None:
            raise ValueError("interval= and at= are mutually exclusive; pass only one")
        if max_bytes is not None and max_bytes <= 0:
            raise ValueError(f"max_bytes must be positive or None, got {max_bytes!r}")
        if backup_count is not None and backup_count < 0:
            raise ValueError(
                f"backup_count must be >= 0 or None, got {backup_count!r}"
            )

        self._path = Path(path)
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._interval = parse_interval(interval) if interval is not None else None
        self._at = parse_time_of_day(at) if at is not None else None
        self._encoding = encoding
        self._terminator = terminator
        self._clock = clock
        self._wall_clock = wall_clock

        self._lock = threading.Lock()
        self._handle = None
        self._size = 0
        self._closed = False
        # None means "no deadline computed yet"; the first write arms it so
        # construction stays side effect free.
        self._deadline: float | None = None

    # -- properties ---------------------------------------------------------

    @property
    def path(self) -> Path:
        """the live file path."""
        return self._path

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def terminator(self) -> str:
        """line separator sinks should append when building a payload."""
        return self._terminator

    @property
    def size(self) -> int:
        """bytes currently in the live file."""
        with self._lock:
            return self._size

    # -- writing ------------------------------------------------------------

    def write(self, payload: str) -> None:
        """append ``payload`` to the live file, rolling first if due."""
        encoded_length = len(payload.encode(self._encoding))

        with self._lock:
            if self._closed:
                raise RuntimeError(f"write on a closed RotatingFile ({self._path})")

            rolled = self._roll_time_locked()
            if (
                not rolled
                and self._max_bytes is not None
                and self._size
                and self._size + encoded_length > self._max_bytes
            ):
                self._rotate_locked()

            handle = self._open_locked()
            handle.write(payload)
            self._size += encoded_length

    def flush(self) -> None:
        """push buffered bytes to the OS. cheap no-op if nothing is open."""
        with self._lock:
            if self._handle is not None and not self._closed:
                self._handle.flush()

    def close(self) -> None:
        """flush and close the file handle. idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            handle = self._handle
            self._handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError as exc:  # pragma: no cover - best effort teardown
                print(f"[toomar] closing {self._path} failed: {exc}", file=sys.stderr)

    # -- rotation -----------------------------------------------------------

    def rotate(self) -> None:
        """roll the live file now. safe to call when the file does not exist."""
        with self._lock:
            if self._closed:
                raise RuntimeError(f"rotate on a closed RotatingFile ({self._path})")
            self._rotate_locked()

    def generations(self) -> list[Path]:
        """rotated files, newest first (``app.log.1``, ``app.log.2``, ...)."""
        with self._lock:
            return [path for _, path in self._generations_locked()]

    def aged_entries(self, now: float, archive_after: float) -> list[tuple[Path, float]]:
        """``(path, mtime)`` for generations older than ``now - archive_after``.

        the stat happens under the instance lock so a concurrent roll cannot
        rename a generation between the listing and the ``stat``.
        """
        cutoff = now - archive_after
        with self._lock:
            entries: list[tuple[Path, float]] = []
            for _, path in self._generations_locked():
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime < cutoff:
                    entries.append((path, mtime))
            return entries

    def aged(self, now: float, archive_after: float) -> list[Path]:
        """paths of generations older than ``now - archive_after``."""
        return [path for path, _ in self.aged_entries(now, archive_after)]

    def discard(self, path: str | os.PathLike[str]) -> None:
        """delete a generation, typically right after archiving it."""
        target = Path(path)
        with self._lock:
            try:
                target.unlink()
            except FileNotFoundError:
                pass

    # -- internals ----------------------------------------------------------

    def _open_locked(self):
        if self._handle is not None:
            return self._handle
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" keeps the bytes we count and the bytes on disk identical.
        self._handle = self._path.open("a", encoding=self._encoding, newline="")
        try:
            self._size = self._path.stat().st_size
        except OSError:  # pragma: no cover - stat right after open
            self._size = 0
        return self._handle

    def _roll_time_locked(self) -> bool:
        if self._interval is None and self._at is None:
            return False

        now = self._clock()
        if self._deadline is None:
            self._deadline = self._deadline_for_locked(now)
            return False
        if now < self._deadline:
            return False

        self._rotate_locked()
        self._deadline = self._deadline_for_locked(self._clock())
        return True

    def _deadline_for_locked(self, now: float) -> float:
        if self._interval is not None:
            return now + self._interval
        assert self._at is not None
        now_wall = self._wall_clock()
        moment = datetime.fromtimestamp(now_wall)
        candidate = moment.replace(
            hour=self._at.hour,
            minute=self._at.minute,
            second=self._at.second,
            microsecond=0,
        )
        if candidate <= moment:
            candidate += timedelta(days=1)
        return now + max(0.0, candidate.timestamp() - now_wall)

    def _generations_locked(self) -> list[tuple[int, Path]]:
        parent = self._path.parent
        prefix = self._path.name + "."
        found: list[tuple[int, Path]] = []
        try:
            children = list(parent.glob(prefix + "[0-9]*"))
        except OSError:  # pragma: no cover - unreadable directory
            return []
        for child in children:
            index = _generation_index(child, self._path)
            if index is not None:
                found.append((index, child))
        found.sort()
        return found

    def _rotate_locked(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError:  # pragma: no cover - best effort
                pass

        base = self._path
        self._size = 0

        if self._backup_count == 0:
            # "keep nothing": the live file is simply replaced.
            try:
                base.unlink()
            except FileNotFoundError:
                pass
            return

        if not base.exists():
            return

        if self._backup_count is None:
            top = max((index for index, _ in self._generations_locked()), default=0)
        else:
            oldest = base.with_name(f"{base.name}.{self._backup_count}")
            try:
                oldest.unlink()
            except FileNotFoundError:
                pass
            top = self._backup_count - 1

        # shift <base>.top -> <base>.top+1, walking down so nothing is clobbered
        for index in range(top, 0, -1):
            source = base.with_name(f"{base.name}.{index}")
            if source.exists():
                os.replace(source, base.with_name(f"{base.name}.{index + 1}"))

        os.replace(base, base.with_name(f"{base.name}.1"))


class Archiver:
    """zips aged generations into one bundle per stream per day.

    a daemon thread wakes every ``check_interval`` seconds and calls
    :meth:`sweep`. generations are grouped by their **file mtime** day, so a
    bundle is named after the day the data actually landed on disk, not the day
    the sweep happened to run. bundles are opened in append mode and entries
    already present are skipped, so re-sweeping never duplicates them.

    :meth:`sweep` is synchronous and public: tests and cron style callers can
    drive archiving deterministically without the thread at all.
    """

    __slots__ = (
        "_after",
        "_check_interval",
        "_sources",
        "_stop",
        "_thread",
    )

    def __init__(
        self,
        sources: Sequence[RotatingFile],
        *,
        after: float,
        check_interval: float = 600.0,
    ) -> None:
        if after is None or after <= 0:
            raise ValueError(f"archive_after must be positive, got {after!r}")
        if check_interval is None or check_interval <= 0:
            raise ValueError(f"archive_check must be positive, got {check_interval!r}")

        self._sources = list(sources)
        self._after = float(after)
        self._check_interval = float(check_interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def sources(self) -> list[RotatingFile]:
        return list(self._sources)

    @property
    def after(self) -> float:
        return self._after

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """start the daemon thread. a no-op if it is already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="toomar-archiver", daemon=True
        )
        self._thread.start()

    def sweep(self) -> int:
        """archive everything aged past ``after`` seconds; return files archived.

        safe to call from the main thread at any time, with or without the
        background thread running.
        """
        now = time.time()
        archived = 0
        for source in self._sources:
            entries = source.aged_entries(now, self._after)
            if not entries:
                continue

            by_day: dict[str, list[Path]] = {}
            for path, mtime in entries:
                day = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
                by_day.setdefault(day, []).append(path)

            for day, paths in by_day.items():
                archived += self._archive_day(source, day, paths)
        return archived

    def close(self) -> None:
        """stop the thread and wait briefly for it. idempotent, never raises."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    # -- internals ----------------------------------------------------------

    def _archive_day(self, source: RotatingFile, day: str, paths: Iterable[Path]) -> int:
        base = source.path
        bundle = base.with_name(f"{base.stem}-{day}.zip")

        ordered = sorted(paths, key=lambda p: _generation_index(p, base) or 0)
        added = 0
        try:
            with zipfile.ZipFile(bundle, "a") as archive:
                existing = set(archive.namelist())
                for path in ordered:
                    index = _generation_index(path, base)
                    if index is None:
                        continue
                    entry = f"{base.name}.{index}"
                    if entry in existing:
                        continue
                    archive.write(path, entry)
                    existing.add(entry)
                    added += 1
        except OSError as exc:
            print(f"[toomar] archiving {bundle} failed: {exc}", file=sys.stderr)
            return 0

        for path in ordered:
            source.discard(path)
        return added

    def _run(self) -> None:
        while not self._stop.wait(self._check_interval):
            try:
                self.sweep()
            except Exception as exc:  # a sweep must never kill the thread
                print(f"[toomar] archiver sweep failed: {exc}", file=sys.stderr)
