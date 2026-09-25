"""
log sinks — the pluggable backends behind a :class:`~toomar.logger.Logger`.

every sink implements :class:`BaseSink`: a ``flush(logs)`` that receives one
batch of :class:`~toomar.logger.Log` records, and an optional ``close()`` that
releases resources when the process shuts down.

shipped sinks:

* :class:`ConsoleSink` — stdout only, with optional ANSI colour
* :class:`BufferSink`  — in memory, for tests and benchmarks
* :class:`FileSink`    — one rotating file
* :class:`LevelFileSink` — one rotating file per level

nothing here opens a file or starts a thread at import time; that only happens
when a sink is constructed.
"""

from __future__ import annotations

import io
import os
import sys
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from toomar.colors import RESET, fg, supports_color
from toomar.formatter import DEFAULT_FORMAT, compile_format, format_log
from toomar.logger import CRITICAL, DEBUG, ERROR, INFO, WARNING, Log
from toomar.rotation import (
    DEFAULT_BACKUP_COUNT,
    DEFAULT_MAX_BYTES,
    Archiver,
    RotatingFile,
)

__all__ = [
    "DEFAULT_COLORS",
    "BaseSink",
    "BufferSink",
    "ConsoleSink",
    "FileSink",
    "LevelFileSink",
]


def _render(
    logs: list[Log],
    *,
    terminator: str,
    prefixes: dict[str, str],
    reset: str,
) -> str:
    """join a batch into one payload.

    shared by :class:`ConsoleSink` and :class:`BufferSink` so the colour path
    cannot drift between them. when ``prefixes`` is empty the hot path is a
    plain ``join`` over ``str(log)`` with no per log branching.
    """
    if prefixes:
        lines = []
        append = lines.append
        for log in logs:
            text = log.message
            prefix = prefixes.get(log.level)
            if prefix is not None:
                text = prefix + text + reset
            append(text)
        return terminator.join(lines) + terminator
    return terminator.join([log.message for log in logs]) + terminator


class BaseSink(ABC):
    """the sink interface.

    subclasses implement :meth:`flush`, which is always called on the worker
    thread with a non empty batch. :meth:`close` is optional: the default is a
    no-op, and :func:`toomar.logger.shutdown` calls it on every sink.
    """

    @abstractmethod
    def flush(self, logs: list[Log]) -> None:
        """write a batch of logs. must not raise on ordinary write failures."""
        raise NotImplementedError

    def close(self) -> None:
        """release resources. called from ``shutdown()`` and at exit."""
        return


#: default level -> color name used by :class:`ConsoleSink`
DEFAULT_COLORS: dict[str, str] = {
    DEBUG: "gray",
    INFO: "green",
    WARNING: "yellow",
    ERROR: "red",
    CRITICAL: "bright_magenta",
}


def _build_prefixes(color_map: dict[str, str] | None) -> dict[str, str]:
    names = DEFAULT_COLORS if color_map is None else color_map
    return {level: fg(name) for level, name in names.items() if name}


class ConsoleSink(BaseSink):
    """write batches of logs to **stdout**.

    the console sink is stdout only — it never opens, rotates or archives
    files. use :class:`FileSink` or :class:`LevelFileSink` for disk output.

    the whole batch is rendered and handed to the stream in a single ``write``
    call, so the per log cost stays close to the cost of building the line
    itself. instances are slotted and nothing is resolved at import time, so
    adding one does not add import time or per instance memory overhead.

    ``sys.stdout`` is resolved on *every* flush rather than captured in
    ``__init__``, so ``contextlib.redirect_stdout``, pytest capture, notebooks
    and embedded interpreters all keep working.

    colour is automatic: escapes are emitted only when stdout is an interactive
    terminal that understands them, so piping stays clean. force it either way
    with ``colors=True`` / ``colors=False``.

    :param autoflush: flush stdout after each batch. set to ``False`` when
        stdout is a file or a pipe and the flush syscall is pure overhead.
    :param terminator: appended after the last line of a batch.
    :param colors: ``None`` auto detects, ``True``/``False`` force it.
    :param color_map: level -> color name overrides.
    """

    __slots__ = ("_autoflush", "_colors", "_prefixes", "_reset", "_terminator")

    def __init__(
        self,
        *,
        autoflush: bool = True,
        terminator: str = "\n",
        colors: bool | None = None,
        color_map: dict[str, str] | None = None,
    ) -> None:
        self._autoflush = autoflush
        self._terminator = terminator
        self._colors = colors
        # escape codes are built once here; the flush path only concatenates
        self._prefixes = _build_prefixes(color_map)
        self._reset = RESET

    def flush(self, logs: list[Log]) -> None:
        if not logs:
            return

        stream = sys.stdout
        colorize = self._colors
        if colorize is None:
            colorize = supports_color(stream)

        stream.write(
            _render(
                logs,
                terminator=self._terminator,
                prefixes=self._prefixes if colorize else {},
                reset=self._reset,
            )
        )

        if self._autoflush:
            stream.flush()


class BufferSink(BaseSink):
    """collect batches in memory as a string.

    the in memory counterpart of :class:`ConsoleSink`, for tests, benchmarks
    and anything that wants to inspect output instead of printing it. this is
    the sink to reach for where you would have passed
    ``ConsoleSink(stream=StringIO())`` — no file descriptor, no syscalls, and
    ``getvalue()`` is a plain read of an in process buffer.

    :param colors: force ANSI colouring on or off. the default is off, because
        a buffer is almost never a terminal.
    :param terminator: appended after each batch.
    :param color_map: level -> color name overrides.
    """

    __slots__ = ("_buffer", "_colors", "_lock", "_prefixes", "_reset", "_terminator")

    def __init__(
        self,
        *,
        colors: bool = False,
        terminator: str = "\n",
        color_map: dict[str, str] | None = None,
    ) -> None:
        self._buffer = io.StringIO()
        self._colors = colors
        self._terminator = terminator
        self._prefixes = _build_prefixes(color_map) if colors else {}
        self._reset = RESET
        # the worker thread writes while the caller reads
        self._lock = threading.Lock()

    def flush(self, logs: list[Log]) -> None:
        if not logs:
            return
        payload = _render(
            logs,
            terminator=self._terminator,
            prefixes=self._prefixes if self._colors else {},
            reset=self._reset,
        )
        with self._lock:
            self._buffer.write(payload)

    def getvalue(self) -> str:
        """everything buffered so far, as one string."""
        with self._lock:
            return self._buffer.getvalue()

    def clear(self) -> None:
        """drop the buffer."""
        with self._lock:
            self._buffer.seek(0)
            self._buffer.truncate(0)

    def __len__(self) -> int:
        """number of characters buffered."""
        with self._lock:
            return len(self._buffer.getvalue())


def _validate_format(fmt: str | None) -> None:
    """compile the format now so a bad template fails at construction."""
    if fmt is not None:
        compile_format(fmt)


class FileSink(BaseSink):
    """write batches to a single file, with size and/or time rotation.

    a thin, opinionated wrapper around :class:`~toomar.rotation.RotatingFile`:
    format the batch, hand it over as one payload, flush once. file handles are
    opened lazily on the first write and parent directories are created then.

    rotated generations are kept on disk (``app.log.1``, ``app.log.2``, ...).
    setting ``archive_after`` starts a daemon thread that zips generations older
    than that many seconds into ``<stem>-<YYYY-MM-DD>.zip`` — one bundle per
    stream per day, named after the day the data landed on disk.

    :param path: file to append to. parents are created on first write.
    :param max_bytes: roll once the live file would exceed this. ``None``
        disables size rotation. default 10 MiB.
    :param backup_count: generations kept on disk. ``None`` keeps every
        generation, ``0`` overwrites the live file on every roll.
    :param interval: relative time rotation, e.g. ``"1h"``, ``"30m"``, ``3600``.
    :param at: wall clock rotation, e.g. ``"00:00"`` or a ``datetime.time``.
        mutually exclusive with ``interval``.
    :param archive_after: seconds a generation must age before it is zipped and
        removed. ``None`` disables archiving.
    :param archive_check: how often the archiver thread sweeps, in seconds.
    :param format: line template, or ``None`` for the bare message.
    :param encoding: file encoding.
    :param terminator: appended after the last line of each batch.
    """

    __slots__ = ("_archiver", "_file", "_format", "_terminator")

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_bytes: int | None = DEFAULT_MAX_BYTES,
        backup_count: int | None = DEFAULT_BACKUP_COUNT,
        interval: str | float | None = None,
        at: str | None = None,
        archive_after: float | None = None,
        archive_check: float = 600.0,
        format: str | None = DEFAULT_FORMAT,
        encoding: str = "utf-8",
        terminator: str = "\n",
    ) -> None:
        _validate_format(format)
        self._format = format
        self._file = RotatingFile(
            path,
            max_bytes=max_bytes,
            backup_count=backup_count,
            interval=interval,
            at=at,
            encoding=encoding,
            terminator=terminator,
        )
        self._terminator = terminator
        self._archiver = (
            None
            if archive_after is None
            else Archiver([self._file], after=archive_after, check_interval=archive_check)
        )
        if self._archiver is not None:
            self._archiver.start()

    @property
    def path(self) -> Path:
        """the live file this sink writes to."""
        return self._file.path

    @property
    def rotating_file(self) -> RotatingFile:
        """the underlying writer, for rolling by hand or inspecting size."""
        return self._file

    @property
    def archiver(self) -> Archiver | None:
        """the background archiver, or ``None`` when archiving is disabled."""
        return self._archiver

    def flush(self, logs: list[Log]) -> None:
        if not logs:
            return
        fmt = self._format
        if fmt is None:
            lines = [log.message for log in logs]
        else:
            lines = [format_log(log, fmt) for log in logs]
        self._file.write(self._terminator.join(lines) + self._terminator)
        self._file.flush()

    def rotate(self) -> None:
        """roll the file immediately."""
        self._file.rotate()

    def sweep(self) -> int:
        """run one archive sweep synchronously; returns files archived."""
        if self._archiver is None:
            return 0
        return self._archiver.sweep()

    def close(self) -> None:
        """stop the archiver, then close the file. idempotent."""
        if self._archiver is not None:
            self._archiver.close()
        self._file.close()


class LevelFileSink(BaseSink):
    """route each log to its own file, chosen by exact level match.

    ``files`` maps a level name to a file name, relative to ``directory`` unless
    absolute::

        LevelFileSink("logs", {"ERROR": "error.log", "INFO": "info.log"})

    an ``ERROR`` record goes to ``logs/error.log`` and an ``INFO`` record to
    ``logs/info.log``. matching is **exact**: a level absent from ``files`` is
    dropped rather than folded into a neighbour, so ``WARNING`` records are not
    silently written to the error file.

    a batch is walked once, accumulating lines per writer, then each writer
    gets a single ``write`` — one ``write`` syscall per file per batch, and no
    line is ever formatted twice.

    rotation and archiving options are identical to :class:`FileSink`, and the
    single archiver thread covers every file this sink owns.

    :param directory: base directory for relative entries in ``files``. created
        lazily on first write.
    :param files: level name -> file name.
    :param max_bytes: per file roll threshold. ``None`` disables it.
    :param backup_count: generations kept per file.
    :param interval: relative time rotation, e.g. ``"1h"``.
    :param at: wall clock rotation, e.g. ``"00:00"``. exclusive with ``interval``.
    :param archive_after: age in seconds before a generation is zipped and
        removed. ``None`` disables archiving.
    :param archive_check: archiver sweep period in seconds.
    :param format: line template, or ``None`` for the bare message.
    :param encoding: file encoding.
    :param terminator: appended after the last line written to each file.
    """

    __slots__ = ("_archiver", "_files", "_format", "_terminator", "_writers")

    def __init__(
        self,
        directory: str | os.PathLike[str],
        files: dict[str, str],
        *,
        max_bytes: int | None = DEFAULT_MAX_BYTES,
        backup_count: int | None = DEFAULT_BACKUP_COUNT,
        interval: str | float | int | None = None,
        at: str | None = None,
        archive_after: float | None = None,
        archive_check: float = 600.0,
        format: str | None = DEFAULT_FORMAT,  # noqa: A002 - mirrors logging
        encoding: str = "utf-8",
        terminator: str = "\n",
    ) -> None:
        if not files:
            raise ValueError("files must map at least one level to a file")

        _validate_format(format)
        self._format = format
        self._terminator = terminator

        base = Path(directory)
        self._files: dict[str, Path] = {}
        seen: dict[str, str] = {}
        for level, name in files.items():
            if not isinstance(level, str) or not level:
                raise ValueError(f"invalid level key {level!r}")
            target = Path(name)
            if not target.is_absolute():
                target = base / target
            key = os.path.normpath(os.path.abspath(target))
            if key in seen:
                raise ValueError(
                    f"levels {seen[key]!r} and {level!r} both write to {key!r}"
                )
            seen[key] = level
            self._files[level] = target

        self._writers: dict[str, RotatingFile] = {
            level: RotatingFile(
                target,
                max_bytes=max_bytes,
                backup_count=backup_count,
                interval=interval,
                at=at,
                encoding=encoding,
                terminator=terminator,
            )
            for level, target in self._files.items()
        }
        self._archiver = (
            None
            if archive_after is None
            else Archiver(
                list(self._writers.values()),
                after=archive_after,
                check_interval=archive_check,
            )
        )
        if self._archiver is not None:
            self._archiver.start()

    @property
    def files(self) -> dict[str, Path]:
        """level -> resolved file path."""
        return dict(self._files)

    @property
    def archiver(self) -> Archiver | None:
        """the background archiver covering every file, or ``None``."""
        return self._archiver

    def flush(self, logs: list[Log]) -> None:
        if not logs:
            return

        writers = self._writers
        fmt = self._format
        groups: dict[RotatingFile, list[str]] = {}

        for log in logs:
            writer = writers.get(log.level)
            if writer is None:
                # level not listed in files: dropped by design
                continue
            line = log.message if fmt is None else format_log(log, fmt)
            bucket = groups.get(writer)
            if bucket is None:
                groups[writer] = [line]
            else:
                bucket.append(line)

        terminator = self._terminator
        for writer, lines in groups.items():
            writer.write(terminator.join(lines) + terminator)
            writer.flush()

    def rotate(self, level: str | None = None) -> None:
        """roll one level's file, or every file when ``level`` is ``None``."""
        if level is None:
            for writer in self._writers.values():
                writer.rotate()
            return
        writer = self._writers.get(level)
        if writer is None:
            raise KeyError(f"no file configured for level {level!r}")
        writer.rotate()

    def sweep(self) -> int:
        """run one archive sweep synchronously; returns files archived."""
        if self._archiver is None:
            return 0
        return self._archiver.sweep()

    def close(self) -> None:
        """stop the archiver, then close every file. idempotent."""
        if self._archiver is not None:
            self._archiver.close()
        for writer in self._writers.values():
            writer.close()
