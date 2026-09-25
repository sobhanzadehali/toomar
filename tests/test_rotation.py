"""RotatingFile size rotation: shifting, backup_count policy, edge cases."""

from __future__ import annotations

import os
import unittest

from toomar.rotation import RotatingFile

from helpers import TmpDirTestCase, generations, names


class SizeRotationTests(TmpDirTestCase):

    def _sink(self, **kwargs) -> RotatingFile:
        kwargs.setdefault("max_bytes", 200)
        kwargs.setdefault("backup_count", 2)
        sink = RotatingFile(self.tmp / "app.log", **kwargs)
        self.addCleanup(sink.close)
        return sink

    def _tag(self, index: int) -> None:
        """append a marker to the live file so generations are distinguishable."""
        with open(self.tmp / "app.log", "a", encoding="utf-8") as handle:
            handle.write(f"gen{index}\n")

    def test_no_rotation_below_the_threshold(self) -> None:
        sink = self._sink()
        for _ in range(5):
            sink.write("x" * 30 + "\n")
        sink.flush()

        self.assertEqual(names(self.tmp), ["app.log"])
        self.assertEqual(sink.size, 5 * 31)

    def test_rotates_when_the_payload_would_overflow(self) -> None:
        sink = self._sink()
        # 31 bytes per write, 200 byte ceiling -> 6 writes per generation
        for _ in range(20):
            sink.write("x" * 30 + "\n")
        sink.flush()

        # the live file plus backup_count generations
        self.assertEqual(generations(self.tmp), ["app.log.1", "app.log.2"])
        self.assertEqual(sink.size, 2 * 31)

    def test_generations_shift_and_the_oldest_is_deleted(self) -> None:
        sink = self._sink()
        for index in range(4):
            sink.write(f"gen{index}\n")
            sink.flush()
            sink.rotate()

        self.assertEqual((self.tmp / "app.log.1").read_text(), "gen3\n")
        self.assertEqual((self.tmp / "app.log.2").read_text(), "gen2\n")
        # gen0 and gen1 were the oldest and backup_count=2 dropped them
        self.assertFalse((self.tmp / "app.log.3").exists())

    def test_backup_count_none_keeps_every_generation(self) -> None:
        sink = self._sink(backup_count=None)
        for index in range(4):
            sink.write(f"gen{index}\n")
            sink.flush()
            sink.rotate()

        self.assertEqual(
            generations(self.tmp),
            ["app.log.1", "app.log.2", "app.log.3", "app.log.4"],
        )
        self.assertEqual((self.tmp / "app.log.4").read_text(), "gen0\n")

    def test_backup_count_one_keeps_a_single_generation(self) -> None:
        sink = self._sink(backup_count=1)
        for index in range(3):
            sink.write(f"gen{index}\n")
            sink.flush()
            sink.rotate()

        self.assertEqual(generations(self.tmp), ["app.log.1"])
        self.assertEqual((self.tmp / "app.log.1").read_text(), "gen2\n")

    def test_backup_count_zero_overwrites_the_live_file(self) -> None:
        sink = self._sink(backup_count=0)
        sink.write("first\n")
        sink.flush()
        sink.rotate()
        sink.write("second\n")
        sink.flush()

        self.assertEqual(names(self.tmp), ["app.log"])
        self.assertEqual((self.tmp / "app.log").read_text(), "second\n")

    def test_payload_larger_than_max_bytes_still_lands(self) -> None:
        sink = self._sink(max_bytes=10)
        sink.write("y" * 500 + "\n")
        sink.flush()

        # a single oversized payload is written rather than rotated away
        self.assertEqual((self.tmp / "app.log").read_text(), "y" * 500 + "\n")
        self.assertEqual(names(self.tmp), ["app.log"])

    def test_max_bytes_none_disables_size_rotation(self) -> None:
        sink = self._sink(max_bytes=None)
        for _ in range(20):
            sink.write("z" * 300 + "\n")
        sink.flush()

        self.assertEqual(names(self.tmp), ["app.log"])

    def test_rotate_on_a_missing_file_is_a_noop(self) -> None:
        sink = self._sink()
        sink.rotate()
        self.assertEqual(names(self.tmp), [])

    def test_size_accounting_uses_encoded_bytes(self) -> None:
        sink = self._sink(max_bytes=None)
        # "é" is two bytes in utf-8 but one character
        sink.write("é\n")
        sink.flush()

        self.assertEqual(sink.size, 3)
        self.assertEqual(os.path.getsize(self.tmp / "app.log"), 3)

    def test_lazy_open_creates_parents_on_first_write(self) -> None:
        path = self.tmp / "a" / "b" / "app.log"
        sink = RotatingFile(path)
        sink.close()
        self.assertFalse((self.tmp / "a").exists())

        sink = RotatingFile(path)
        self.addCleanup(sink.close)
        sink.write("hello\n")
        sink.close()
        self.assertEqual(path.read_text(), "hello\n")

    def test_appends_to_a_pre_existing_file(self) -> None:
        path = self.tmp / "app.log"
        path.write_text("existing\n")

        sink = self._sink()
        sink.write("new\n")
        sink.flush()

        self.assertEqual(path.read_text(), "existing\nnew\n")
        # the running size picked up the bytes already on disk
        self.assertEqual(sink.size, len("existing\nnew\n"))

    def test_generations_lists_newest_first(self) -> None:
        sink = self._sink(backup_count=None)
        for index in range(3):
            sink.write(f"{index}\n")
            sink.flush()
            sink.rotate()

        self.assertEqual(
            [p.name for p in sink.generations()],
            ["app.log.1", "app.log.2", "app.log.3"],
        )

    def test_generations_ignores_unrelated_files(self) -> None:
        sink = self._sink()
        sink.write("a\n")
        sink.flush()
        sink.rotate()
        (self.tmp / "app.log.bak").write_text("junk")
        (self.tmp / "app.log.9x").write_text("junk")

        self.assertEqual([p.name for p in sink.generations()], ["app.log.1"])

    def test_close_is_idempotent_and_reports_state(self) -> None:
        sink = self._sink()
        self.assertFalse(sink.closed)
        sink.write("x\n")
        sink.close()
        sink.close()
        self.assertTrue(sink.closed)

    def test_invalid_arguments(self) -> None:
        with self.assertRaises(ValueError):
            RotatingFile(self.tmp / "app.log", max_bytes=0)
        with self.assertRaises(ValueError):
            RotatingFile(self.tmp / "app.log", backup_count=-1)
        with self.assertRaises(ValueError):
            RotatingFile(self.tmp / "app.log", interval="0s")
        with self.assertRaises(ValueError):
            RotatingFile(self.tmp / "app.log", interval="banana")

    def test_terminator_property(self) -> None:
        sink = self._sink(terminator="\r\n")
        self.assertEqual(sink.terminator, "\r\n")

    def test_path_property(self) -> None:
        sink = self._sink()
        self.assertEqual(sink.path, self.tmp / "app.log")


if __name__ == "__main__":
    unittest.main()
