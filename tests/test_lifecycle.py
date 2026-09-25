"""worker lifecycle: shutdown closes sinks, atexit is hooked, late logs vanish."""

from __future__ import annotations

import atexit
import io
import unittest
from contextlib import redirect_stderr

import toomar.logger as logger_module
from toomar.conf import Config
from toomar.logger import get_logger, shutdown
from toomar.sinks import BaseSink, BufferSink

from helpers import TmpDirTestCase


class RecordingSink(BaseSink):
    """a sink that records batches and whether it was closed."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []
        self.closed = 0

    def flush(self, logs) -> None:
        self.batches.append([str(log) for log in logs])

    def close(self) -> None:
        self.closed += 1


class ExplodingCloseSink(BufferSink):
    def close(self) -> None:
        raise RuntimeError("nope")


class ShutdownTests(unittest.TestCase):
    """these tests drive the process wide worker, so they run in order."""

    def setUp(self) -> None:
        shutdown()  # start from a clean slate
        self.addCleanup(shutdown)

    def _fresh_logger(self, sink: BaseSink, name: str = "lifecycle"):
        # a new name bypasses the logger cache, but the worker is a singleton,
        # so the previous shutdown() above is what makes this config take effect
        cfg = Config(sinks=[sink], format=None)
        return get_logger(name, config=cfg)

    def test_shutdown_closes_sinks(self) -> None:
        sink = RecordingSink()
        log = self._fresh_logger(sink, "closes")
        log.info("hello")
        shutdown()

        self.assertEqual(sink.closed, 1)
        self.assertEqual(sink.batches, [["hello"]])

    def test_shutdown_is_idempotent(self) -> None:
        sink = RecordingSink()
        self._fresh_logger(sink, "idempotent")
        shutdown()
        shutdown()
        shutdown()

        self.assertEqual(sink.closed, 1)

    def test_atexit_hook_is_registered(self) -> None:
        registered = atexit._ncallbacks()  # noqa: SLF001 - stdlib introspection
        self.assertGreater(registered, 0)
        # shutting down explicitly must not unregister the exit hook
        shutdown()
        self.assertEqual(atexit._ncallbacks(), registered)  # noqa: SLF001

    def test_logging_after_shutdown_is_a_silent_noop(self) -> None:
        sink = RecordingSink()
        log = self._fresh_logger(sink, "after-shutdown")
        log.info("delivered")
        shutdown()
        log.info("dropped")
        log.error("also dropped")

        self.assertEqual(sink.batches, [["delivered"]])

    def test_a_failing_close_warns_and_does_not_propagate(self) -> None:
        good = RecordingSink()
        bad = ExplodingCloseSink()
        cfg = Config(sinks=[bad, good], format=None)
        get_logger("failing-close", config=cfg)

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            shutdown()

        self.assertIn("ExplodingCloseSink", stderr.getvalue())
        self.assertIn("nope", stderr.getvalue())
        # the healthy sink behind the broken one still got closed
        self.assertEqual(good.closed, 1)

    def test_a_failing_flush_warns_and_does_not_propagate(self) -> None:
        class BadFlush(BaseSink):
            def flush(self, logs) -> None:
                raise ValueError("disk on fire")

        good = RecordingSink()
        log = get_logger(
            "failing-flush", config=Config(sinks=[BadFlush(), good], format=None)
        )
        log.info("boom")

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            shutdown()

        self.assertIn("BadFlush", stderr.getvalue())
        self.assertIn("disk on fire", stderr.getvalue())
        self.assertEqual(good.batches, [["boom"]])
        self.assertEqual(good.closed, 1)

    def test_shutdown_drains_logs_queued_behind_the_sentinel(self) -> None:
        sink = RecordingSink()
        log = self._fresh_logger(sink, "drain")
        for index in range(250):
            log.info(str(index))
        shutdown()

        delivered = [message for batch in sink.batches for message in batch]
        self.assertEqual(delivered, [str(index) for index in range(250)])

    def test_a_sink_without_close_is_tolerated(self) -> None:
        class NoCloseSink(BaseSink):
            def flush(self, logs) -> None:
                pass

        get_logger("no-close", config=Config(sinks=[NoCloseSink()], format=None))
        with redirect_stderr(io.StringIO()) as stderr:
            shutdown()
        self.assertEqual(stderr.getvalue(), "")


class WorkerUnitTests(TmpDirTestCase):

    def test_worker_close_without_start(self) -> None:
        sink = RecordingSink()
        worker = logger_module._LogWorker([sink])  # noqa: SLF001
        worker.close()
        self.assertEqual(sink.closed, 1)

    def test_base_sink_close_is_a_noop(self) -> None:
        class Minimal(BaseSink):
            def flush(self, logs) -> None:
                pass

        self.assertIsNone(Minimal().close())


if __name__ == "__main__":
    unittest.main()
