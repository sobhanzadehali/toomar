"""
toomar — a fast, dependency free logging framework for Python.

async by default (one worker thread batches the writes), colored by default on
capable terminals, and able to rotate and archive files without pulling in
``logging.handlers``::

    from toomar import LevelFileSink, get_logger, shutdown

    log = get_logger("app")
    log.info("ready")

for the full sink reference see ``docs/sinks.md``.
"""

from toomar.conf import Config
from toomar.logger import (
    CRITICAL,
    DEBUG,
    ERROR,
    INFO,
    LEVELS,
    WARNING,
    Log,
    Logger,
    get_logger,
    shutdown,
)
from toomar.rotation import Archiver, RotatingFile
from toomar.sinks import (
    BaseSink,
    BufferSink,
    ConsoleSink,
    FileSink,
    LevelFileSink,
)

__all__ = [
    "CRITICAL",
    "DEBUG",
    "ERROR",
    "INFO",
    "LEVELS",
    "WARNING",
    "Archiver",
    "BaseSink",
    "BufferSink",
    "Config",
    "ConsoleSink",
    "FileSink",
    "LevelFileSink",
    "Log",
    "Logger",
    "RotatingFile",
    "get_logger",
    "shutdown",
]
