"""LevelFileSink: exact level routing, one write per file per batch."""

from __future__ import annotations

import unittest
from datetime import datetime

from toomar.logger import CRITICAL, DEBUG, ERROR, INFO, WARNING

from toomar.sinks import LevelFileSink

from helpers import TmpDirTestCase, make_log, make_logs


class LevelRoutingTests(TmpDirTestCase):

    def _sink(self, **kwargs) -> LevelFileSink:
        return LevelFileSink(self.tmp, {"ERROR": "error.log", "INFO": "info.log"}, **kwargs)

    def test_exact_routing(self) -> None:
        sink = self._sink(format="{message}")
        sink.flush(make_logs(("bad", ERROR), ("fine", INFO)))
        sink.close()

        self.assertEqual((self.tmp / "error.log").read_text(), "bad\n")
        self.assertEqual((self.tmp / "info.log").read_text(), "fine\n")

    def test_unlisted_levels_are_dropped(self) -> None:
        sink = self._sink(format="{message}")
        sink.flush(
            make_logs(
                ("a", DEBUG),
                ("b", INFO),
                ("c", WARNING),
                ("d", ERROR),
                ("e", CRITICAL),
            )
        )
        sink.close()

        # WARNING is not ERROR: no threshold folding
        self.assertEqual((self.tmp / "error.log").read_text(), "d\n")
        self.assertEqual((self.tmp / "info.log").read_text(), "b\n")
        self.assertFalse((self.tmp / "warning.log").exists())

    def test_three_files_in_one_batch(self) -> None:
        sink = LevelFileSink(
            self.tmp,
            {"DEBUG": "debug.log", "INFO": "info.log", "ERROR": "error.log"},
            format="{message}",
        )
        sink.flush(
            make_logs(("d", DEBUG), ("i", INFO), ("e", ERROR), ("d2", DEBUG))
        )
        sink.close()

        self.assertEqual((self.tmp / "debug.log").read_text(), "d\nd2\n")
        self.assertEqual((self.tmp / "info.log").read_text(), "i\n")
        self.assertEqual((self.tmp / "error.log").read_text(), "e\n")

    def test_one_write_per_file_per_batch(self) -> None:
        sink = self._sink(format="{message}")
        calls: list[str] = []

        class CountingWriter:
            """wraps a RotatingFile and records every write payload."""

            def __init__(self, inner):
                self._inner = inner

            @property
            def path(self):
                return self._inner.path

            def write(self, payload: str) -> None:
                calls.append(payload)
                self._inner.write(payload)

            def flush(self) -> None:
                self._inner.flush()

            def close(self) -> None:
                self._inner.close()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        writers = sink._writers  # noqa: SLF001
        writers[ERROR] = CountingWriter(writers[ERROR])
        writers[INFO] = CountingWriter(writers[INFO])

        sink.flush(make_logs(("a", INFO), ("b", INFO), ("c", ERROR), ("d", ERROR)))
        sink.close()

        self.assertEqual(len(calls), 2)
        self.assertEqual(sorted(calls), ["a\nb\n", "c\nd\n"])

    def test_relative_paths_resolve_under_directory(self) -> None:
        sink = LevelFileSink(self.tmp, {"ERROR": "sub/dir/error.log"}, format="{message}")
        sink.flush(make_logs(("x", ERROR)))
        sink.close()

        self.assertEqual((self.tmp / "sub" / "dir" / "error.log").read_text(), "x\n")

    def test_absolute_paths_are_used_as_given(self) -> None:
        target = self.tmp / "absolute.log"
        sink = LevelFileSink(self.tmp, {"ERROR": str(target)}, format="{message}")
        sink.flush(make_logs(("x", ERROR)))
        sink.close()

        self.assertEqual(target.read_text(), "x\n")

    def test_duplicate_targets_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LevelFileSink(self.tmp, {"ERROR": "same.log", "INFO": "same.log"})

    def test_duplicate_targets_via_different_spellings(self) -> None:
        with self.assertRaises(ValueError):
            LevelFileSink(self.tmp, {"ERROR": "a.log", "INFO": "./a.log"})

    def test_empty_mapping_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LevelFileSink(self.tmp, {})

    def test_default_format(self) -> None:
        sink = self._sink()
        log = make_log("boom", ERROR, "worker")
        sink.flush([log])
        sink.close()

        self.assertEqual(
            (self.tmp / "error.log").read_text(),
            f"{log.created_date.strftime('%Y-%m-%d %H:%M:%S')} ERROR worker boom\n",
        )

    def test_custom_format_and_asctime_pattern(self) -> None:
        sink = self._sink(format="{asctime:%H:%M:%S} {name} {message}")
        log = make_log("boom", ERROR, "worker")
        log.created_date = datetime(2020, 1, 2, 3, 4, 5)
        sink.flush([log])
        sink.close()

        self.assertEqual((self.tmp / "error.log").read_text(), "03:04:05 worker boom\n")

    def test_format_none(self) -> None:
        sink = self._sink(format=None)
        sink.flush(make_logs(("a", INFO), ("b", ERROR)))
        sink.close()

        self.assertEqual((self.tmp / "info.log").read_text(), "a\n")
        self.assertEqual((self.tmp / "error.log").read_text(), "b\n")

    def test_bad_format_raises_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            LevelFileSink(self.tmp, {"ERROR": "e.log"}, format="{bogus}")

    def test_empty_batch_writes_nothing(self) -> None:
        sink = self._sink()
        sink.flush([])
        sink.close()
        self.assertFalse((self.tmp / "error.log").exists())
        self.assertFalse((self.tmp / "info.log").exists())

    def test_close_is_idempotent_and_locks_the_writers(self) -> None:
        sink = self._sink(format="{message}")
        sink.flush(make_logs(("a", INFO)))
        sink.close()
        sink.close()

        for writer in sink._writers.values():  # noqa: SLF001
            self.assertTrue(writer.closed)
        self.assertEqual((self.tmp / "info.log").read_text(), "a\n")

    def test_write_after_close_raises(self) -> None:
        sink = self._sink()
        sink.close()
        with self.assertRaises(RuntimeError):
            sink.flush(make_logs(("late", INFO)))

    def test_files_property_reports_resolved_targets(self) -> None:
        sink = self._sink()
        self.assertEqual(
            sink.files,
            {"ERROR": self.tmp / "error.log", "INFO": self.tmp / "info.log"},
        )
        sink.close()

    def test_per_file_rotation_is_independent(self) -> None:
        sink = LevelFileSink(
            self.tmp,
            {"ERROR": "error.log", "INFO": "info.log"},
            max_bytes=None,
            format="{message}",
        )
        sink.flush(make_logs(("e", ERROR)))
        sink.rotate("ERROR")
        sink.close()

        self.assertTrue((self.tmp / "error.log.1").exists())
        self.assertFalse((self.tmp / "info.log.1").exists())

    def test_rotate_all_and_unknown_level(self) -> None:
        sink = LevelFileSink(
            self.tmp,
            {"ERROR": "error.log", "INFO": "info.log"},
            max_bytes=None,
            format="{message}",
        )
        sink.flush(make_logs(("e", ERROR), ("i", INFO)))
        sink.rotate()
        sink.close()
        self.assertTrue((self.tmp / "error.log.1").exists())
        self.assertTrue((self.tmp / "info.log.1").exists())

        with self.assertRaises(KeyError):
            sink.rotate("NOPE")

    def test_single_archiver_covers_every_file(self) -> None:
        sink = self._sink(archive_after=3600.0)
        self.assertIsNotNone(sink.archiver)
        self.assertEqual(len(sink.archiver.sources), 2)
        sink.close()
        self.assertFalse(sink.archiver.running)

    def test_no_archiver_by_default(self) -> None:
        sink = self._sink()
        self.assertIsNone(sink.archiver)
        self.assertEqual(sink.sweep(), 0)
        sink.close()


if __name__ == "__main__":
    unittest.main()
