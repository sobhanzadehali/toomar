from __future__ import annotations

import sys
import threading
from datetime import datetime
from queue import Empty, Queue
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from toomar.conf import Config
    from toomar.sinks import BaseSink

DEBUG = "DEBUG"
INFO = "INFO"
WARNING = "WARNING"
ERROR = "ERROR"
CRITICAL = "CRITICAL"

LEVELS: tuple[str, ...] = (DEBUG, INFO, WARNING, ERROR, CRITICAL)
_LEVEL_TO_INT: dict[str, int] = {lvl: i for i, lvl in enumerate(LEVELS)}


_loggers: dict[str, Logger] = {}
_loggers_lock = threading.Lock()
_worker: _LogWorker | None = None
_worker_lock = threading.Lock()
_default_config: Config | None = None


class Log:
    __slots__ = ("created_date", "level", "logger_name", "message")

    def __init__(
        self,
        message: str,
        level: str = INFO,
        logger_name: str = "",
    ):
        self.message = message
        self.level = level
        self.created_date = datetime.now()
        self.logger_name = logger_name

    def __str__(self):
        return self.message


def _get_worker(config: Config) -> _LogWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = _LogWorker(config.sinks)
            _worker.start()
        return _worker


def get_logger(name: str = "", config: Config | None = None) -> Logger:
    global _default_config
    if config is not None:
        _default_config = config

    with _loggers_lock:
        if name not in _loggers:
            cfg = _default_config
            if cfg is None:
                from toomar.conf import Config as ConfigClass
                from toomar.sinks import ConsoleSink

                cfg = ConfigClass(sinks=[ConsoleSink()], format=None)
            _loggers[name] = Logger(name, cfg)
        return _loggers[name]


def shutdown() -> None:
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.stop()
            _worker = None


class _LogWorker:
    __slots__ = ("_queue", "_running", "_shutdown_timeout", "_sinks", "_thread")

    def __init__(self, sinks: list[BaseSink], shutdown_timeout: float = 2.0):
        self._queue: Queue[Log | None] = Queue()
        self._sinks = sinks
        self._running = False
        self._thread: threading.Thread | None = None
        self._shutdown_timeout = shutdown_timeout

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self._shutdown_timeout)

    def enqueue(self, log: Log) -> None:
        if self._running:
            self._queue.put(log)

    def _run(self) -> None:
        batch: list[Log] = []
        while self._running:
            try:
                item = self._queue.get(timeout=0.1)
            except Empty:
                if batch:
                    self._flush_batch(batch)
                    batch.clear()
                continue

            if item is None:
                break

            batch.append(item)
            if len(batch) >= 100:
                self._flush_batch(batch)
                batch.clear()

        if batch:
            self._flush_batch(batch)

    def _flush_batch(self, batch: list[Log]) -> None:
        for sink in self._sinks:
            try:
                sink.flush(batch)
            except Exception as e:
                print(f"[toomar] sink {sink.__class__.__name__} failed: {e}", file=sys.stderr)


class Logger:
    __slots__ = ("_config", "_level", "_name", "_worker")

    def __init__(self, name: str, config: Config):
        self._name = name
        self._config = config
        self._level = INFO
        self._worker = _get_worker(config)

    def set_level(self, level: str) -> None:
        self._level = level.upper()

    def _enabled_for(self, level: str) -> bool:
        return _LEVEL_TO_INT.get(level, 1) >= _LEVEL_TO_INT.get(self._level, 1)

    def _log(self, level: str, *args: object) -> None:
        if not self._enabled_for(level):
            return

        if len(args) == 1:
            message = str(args[0])
        else:
            message = " ".join(str(a) for a in args)

        log = Log(message=message, level=level, logger_name=self._name)
        self._worker.enqueue(log)

    def debug(self, *args: object) -> None:
        self._log(DEBUG, *args)

    def info(self, *args: object) -> None:
        self._log(INFO, *args)

    def warning(self, *args: object) -> None:
        self._log(WARNING, *args)

    def error(self, *args: object) -> None:
        self._log(ERROR, *args)

    def critical(self, *args: object) -> None:
        self._log(CRITICAL, *args)

    @property
    def name(self) -> str:
        return self._name
