"""FileSink: line format, lazy directory creation, and the close contract."""

from __future__ import annotations

import unittest
from datetime import datetime

from toomar.formatter import DateTimeFormatter
from toomar.logger import ERROR, INFO
from toomar.sinks import FileSink

from helpers import TmpDirTestCase, make_log, make_logs


class FileSinkTests(TmpDirTestCase):

    def test_default_line_format(self) -> None:
        path = self.tmp / "app.log"
        sink = FileSink(path)
        log = make_log("hello", INFO, "svc")
        sink.flush([log])
        sink.close()

        expected = (
            f"{log.created_date.strftime('%Y-%m-%d %H:%M:%S')} INFO svc hello\n"
        )
        self.assertEqual(path.read_text(), expected)

    def test_asctime_uses_the_logs_created_date(self) -> None:
        path = self.tmp / "app.log"
        sink = FileSink(path, format="{asctime:%Y}|{message}")
        log = make_log("hello", INFO)
        log.created_date = datetime(2021, 3, 4, 5, 6, 7)
        sink.flush([log])
        sink.close()

        self.assertEqual(path.read_text(), "2021|hello\n")

    def test_batch_is_written_in_order(self) -> None:
        path = self.tmp / "app.log"
        sink = FileSink(path, format="{levelname}:{message}")
        sink.flush(make_logs(("a", INFO), ("b", ERROR), ("c", INFO)))
        sink.close()

        self.assertEqual(path.read_text(), "INFO:a\nERROR:b\nINFO:c\n")

    def test_format_none_writes_bare_messages(self) -> None:
        path = self.tmp / "app.log"
        sink = FileSink(path, format=None)
        sink.flush(make_logs(("a", INFO), ("b", ERROR)))
        sink.close()

        self.assertEqual(path.read_text(), "a\nb\n")

    def test_custom_format(self) -> None:
        path = self.tmp / "app.log"
        sink = FileSink(path, format="{name}|{levelname}|{message}")
        sink.flush([make_log("x", ERROR, "worker")])
        sink.close()

        self.assertEqual(path.read_text(), "worker|ERROR|x\n")

    def test_bad_format_raises_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            FileSink(self.tmp / "app.log", format="{nope}")
        with self.assertRaises(ValueError):
            FileSink(self.tmp / "app.log", format="{unclosed")
        with self.assertRaises(ValueError):
            FileSink(self.tmp / "app.log", format="{message!r}")

    def test_datetime_pattern_validation_is_available(self) -> None:
        # CPython's strftime is lenient about unknown directives, but the
        # platform can reject a pattern, so the check stays and is what
        # {asctime:...} runs through
        self.assertTrue(DateTimeFormatter.is_valid_format("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(
            DateTimeFormatter.format(datetime(2020, 1, 2), "%Y/%m/%d"),
            "2020/01/02",
        )

    def test_parent_directories_created_on_first_write(self) -> None:
        path = self.tmp / "deep" / "nested" / "app.log"
        sink = FileSink(path, format="{message}")
        sink.close()
        # close() before any write must not create anything
        self.assertFalse(path.parent.exists())

        sink = FileSink(path, format="{message}")
        sink.flush([make_log("x", INFO)])
        sink.close()
        self.assertTrue(path.exists())

    def test_append_across_sink_instances(self) -> None:
        path = self.tmp / "app.log"
        first = FileSink(path, format="{message}")
        first.flush([make_log("one", INFO)])
        first.close()

        second = FileSink(path, format="{message}")
        second.flush([make_log("two", INFO)])
        second.close()

        self.assertEqual(path.read_text(), "one\ntwo\n")

    def test_empty_batch_writes_nothing(self) -> None:
        path = self.tmp / "app.log"
        sink = FileSink(path)
        sink.flush([])
        sink.close()
        # the handle is opened lazily, so an empty batch leaves no file behind
        self.assertFalse(path.exists())

    def test_close_is_idempotent(self) -> None:
        sink = FileSink(self.tmp / "app.log", format="{message}")
        sink.flush([make_log("a", INFO)])
        sink.close()
        sink.close()
        self.assertEqual((self.tmp / "app.log").read_text(), "a\n")

    def test_write_after_close_raises_runtime_error(self) -> None:
        path = self.tmp / "app.log"
        sink = FileSink(path)
        sink.close()

        # the worker swallows sink exceptions, so assert on the writer itself
        with self.assertRaises(RuntimeError):
            sink.rotating_file.write("late\n")
        with self.assertRaises(RuntimeError):
            sink.rotating_file.rotate()
        with self.assertRaises(RuntimeError):
            sink.flush([make_log("late", INFO)])

    def test_interval_and_at_together_raise(self) -> None:
        with self.assertRaises(ValueError):
            FileSink(self.tmp / "app.log", interval="1h", at="00:00")

    def test_no_archiver_by_default(self) -> None:
        sink = FileSink(self.tmp / "app.log")
        self.assertIsNone(sink.archiver)
        self.assertEqual(sink.sweep(), 0)
        sink.close()

    def test_encoding_is_honoured(self) -> None:
        path = self.tmp / "app.log"
        sink = FileSink(path, format="{message}", encoding="utf-16")
        sink.flush([make_log("héllo", INFO)])
        sink.close()

        self.assertEqual(path.read_text(encoding="utf-16"), "héllo\n")


if __name__ == "__main__":
    unittest.main()
