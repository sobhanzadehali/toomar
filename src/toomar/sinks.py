import sys
from abc import ABC, abstractmethod
from typing import TextIO

from toomar.colors import RESET, fg, supports_color
from toomar.logger import INFO, WARNING, ERROR, CRITICAL, Log


class BaseSink(ABC):

    @abstractmethod
    def flush(self, logs: list[Log]):
        pass


#: default level -> color name used by :class:`ConsoleSink`
DEFAULT_COLORS: dict[str, str] = {
    "DEBUG": "gray",
    INFO: "green",
    WARNING: "yellow",
    ERROR: "red",
    CRITICAL: "bright_magenta",
}


class ConsoleSink(BaseSink):
    """
    writes a batch of logs to the console.

    the whole batch is rendered and handed to the stream with a single
    ``write`` call, so the per log cost stays close to the cost of
    building the line itself. instances are slotted, and nothing is
    resolved at import time, so adding a console sink to a project does
    not add import time or per instance memory overhead.

    color is automatic: escape sequences are only emitted when the target
    stream is an interactive terminal that understands them, so piping
    output to a file or another process stays clean. force it either way
    with ``colors=True`` / ``colors=False``.
    """

    __slots__ = ("_autoflush", "_colors", "_prefixes", "_reset", "_stream",
                 "_terminator")

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        autoflush: bool = True,
        terminator: str = "\n",
        colors: bool | None = None,
        color_map: dict[str, str] | None = None,
    ) -> None:
        # ``None`` means "resolve sys.stdout on every flush" instead of
        # capturing it here, so redirection (``redirect_stdout``, pytest
        # capture, notebooks) keeps working.
        self._stream = stream
        # set to False when the stream is a file or a pipe, to drop the
        # flush syscall per batch.
        self._autoflush = autoflush
        self._terminator = terminator
        # None means "decide per flush from the stream", True/False force it.
        self._colors = colors

        names = DEFAULT_COLORS if color_map is None else color_map
        # escape codes are built once here, the flush path only concatenates
        prefixes = {level: fg(name) for level, name in names.items() if name}
        self._prefixes = prefixes
        self._reset = RESET

    def flush(self, logs: list[Log]) -> None:
        if not logs:
            return

        stream = self._stream
        if stream is None:
            stream = sys.stdout

        colorize = self._colors
        if colorize is None:
            colorize = supports_color(stream)

        terminator = self._terminator
        if colorize:
            reset = self._reset
            prefixes = self._prefixes
            lines = []
            for log in logs:
                text = str(log)
                prefix = prefixes.get(getattr(log, "level", None))
                if prefix is not None:
                    text = prefix + text + reset
                lines.append(text)
            payload = terminator.join(lines) + terminator
        else:
            payload = terminator.join(map(str, logs)) + terminator

        stream.write(payload)

        if self._autoflush:
            stream.flush()
