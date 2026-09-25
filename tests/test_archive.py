"""Archiver: aged generations zipped per day, originals removed, idempotent."""

from __future__ import annotations

import os
import time
import unittest
import zipfile
from datetime import datetime, timedelta

from toomar.rotation import Archiver, RotatingFile

from helpers import TmpDirTestCase


def set_mtime(path, moment: float) -> None:
    os.utime(path, (moment, moment))


def day_of(moment: float) -> str:
    """the bundle day the archiver derives from ``moment``'s mtime."""
    return datetime.fromtimestamp(moment).strftime("%Y-%m-%d")


class ArchiveSweepTests(TmpDirTestCase):

    def _aged_source(self, stem: str, count: int = 2, age: float = 3600.0):
        """build a file with ``count`` aged generations.

        returns ``(source, day)``, where ``day`` is the bundle day the archiver
        will derive from the mtimes set here.
        """
        path = self.tmp / f"{stem}.log"
        source = RotatingFile(path, max_bytes=None)
        # a roll only happens on a live file, so seed one generation per index
        for _ in range(count):
            source.write("payload\n")
            source.flush()
            source.rotate()
        source.write("live\n")
        source.flush()
        source.close()

        old = time.time() - age
        for index in range(1, count + 1):
            set_mtime(self.tmp / f"{stem}.log.{index}", old)
        return source, day_of(old)

    def test_aged_generations_are_zipped_per_day(self) -> None:
        source, day = self._aged_source("error")
        archiver = Archiver([source], after=60.0)

        self.assertEqual(archiver.sweep(), 2)

        bundle = self.tmp / f"error-{day}.zip"
        self.assertTrue(bundle.exists())
        with zipfile.ZipFile(bundle) as archive:
            self.assertEqual(sorted(archive.namelist()), ["error.log.1", "error.log.2"])
            self.assertEqual(archive.read("error.log.1"), b"payload\n")

    def test_archives_carry_the_actual_contents(self) -> None:
        path = self.tmp / "app.log"
        source = RotatingFile(path, max_bytes=None)
        for index in range(3):
            source.write(f"gen{index}\n")
            source.flush()
            source.rotate()
        source.close()

        old = time.time() - 3600
        for index in (1, 2, 3):
            set_mtime(self.tmp / f"app.log.{index}", old)

        archiver = Archiver([source], after=60.0)
        self.assertEqual(archiver.sweep(), 3)

        with zipfile.ZipFile(self.tmp / f"app-{day_of(old)}.zip") as archive:
            self.assertEqual(archive.read("app.log.1"), b"gen2\n")
            self.assertEqual(archive.read("app.log.2"), b"gen1\n")
            self.assertEqual(archive.read("app.log.3"), b"gen0\n")

    def test_originals_are_removed_after_archiving(self) -> None:
        source, _ = self._aged_source("app")
        Archiver([source], after=60.0).sweep()

        self.assertFalse((self.tmp / "app.log.1").exists())
        self.assertFalse((self.tmp / "app.log.2").exists())
        # the live file is never archived, only generations are
        self.assertEqual((self.tmp / "app.log").read_text(), "live\n")

    def test_second_sweep_adds_nothing(self) -> None:
        source, day = self._aged_source("app")
        archiver = Archiver([source], after=60.0)

        self.assertEqual(archiver.sweep(), 2)
        self.assertEqual(archiver.sweep(), 0)

        with zipfile.ZipFile(self.tmp / f"app-{day}.zip") as archive:
            self.assertEqual(len(archive.namelist()), 2)

    def test_young_generations_are_untouched(self) -> None:
        source, day = self._aged_source("app", count=2, age=1.0)
        archiver = Archiver([source], after=3600.0)

        self.assertEqual(archiver.sweep(), 0)
        self.assertTrue((self.tmp / "app.log.1").exists())
        self.assertFalse((self.tmp / f"app-{day}.zip").exists())

    def test_mixed_ages_split_into_two_bundles(self) -> None:
        source, day = self._aged_source("app", count=3, age=7200.0)
        older = (datetime.fromtimestamp(time.time() - 7200) - timedelta(days=1))
        set_mtime(self.tmp / "app.log.3", older.timestamp())

        archiver = Archiver([source], after=60.0)
        self.assertEqual(archiver.sweep(), 3)

        self.assertTrue((self.tmp / f"app-{day}.zip").exists())
        yesterday = self.tmp / f"app-{day_of(older.timestamp())}.zip"
        self.assertTrue(yesterday.exists())
        with zipfile.ZipFile(yesterday) as archive:
            self.assertEqual(archive.namelist(), ["app.log.3"])
        with zipfile.ZipFile(self.tmp / f"app-{day}.zip") as archive:
            self.assertEqual(sorted(archive.namelist()), ["app.log.1", "app.log.2"])

    def test_one_sweep_covers_every_source(self) -> None:
        first, first_day = self._aged_source("error")
        second, second_day = self._aged_source("info", count=1)

        archiver = Archiver([first, second], after=60.0)
        self.assertEqual(archiver.sweep(), 3)

        self.assertTrue((self.tmp / f"error-{first_day}.zip").exists())
        self.assertTrue((self.tmp / f"info-{second_day}.zip").exists())

    def test_appending_to_an_existing_bundle_keeps_entries_unique(self) -> None:
        source, day = self._aged_source("app", count=1)
        archiver = Archiver([source], after=60.0)
        archiver.sweep()

        # a later rotation re-creates generation 1, then ages out again
        again = RotatingFile(self.tmp / "app.log", max_bytes=None)
        again.write("replacement\n")
        again.flush()
        again.rotate()
        again.close()
        set_mtime(self.tmp / "app.log.1", time.time() - 7200)
        self.assertEqual(archiver.sweep(), 0)

        with zipfile.ZipFile(self.tmp / f"app-{day}.zip") as archive:
            self.assertEqual(archive.namelist(), ["app.log.1"])

    def test_live_file_is_never_an_archive_entry(self) -> None:
        source, day = self._aged_source("app", count=1)
        Archiver([source], after=60.0).sweep()

        self.assertTrue((self.tmp / "app.log").exists())
        with zipfile.ZipFile(self.tmp / f"app-{day}.zip") as archive:
            self.assertNotIn("app.log", archive.namelist())

    def test_sources_must_be_valid(self) -> None:
        source = RotatingFile(self.tmp / "app.log")
        self.addCleanup(source.close)
        with self.assertRaises(ValueError):
            Archiver([source], after=0)
        with self.assertRaises(ValueError):
            Archiver([source], after=None)
        with self.assertRaises(ValueError):
            Archiver([source], after=10.0, check_interval=0)

    def test_aged_helper_uses_the_cutoff(self) -> None:
        source, _ = self._aged_source("app", count=2, age=100.0)
        now = time.time()

        self.assertEqual(len(source.aged(now, 60.0)), 2)
        self.assertEqual(source.aged(now, 1000.0), [])


class ArchiverThreadTests(TmpDirTestCase):

    def test_background_thread_sweeps(self) -> None:
        source = RotatingFile(self.tmp / "app.log", max_bytes=None)
        source.write("payload\n")
        source.flush()
        source.rotate()
        source.write("live\n")
        source.flush()
        source.close()
        old = time.time() - 3600
        set_mtime(self.tmp / "app.log.1", old)
        bundle = self.tmp / f"app-{day_of(old)}.zip"

        archiver = Archiver([source], after=0.1, check_interval=0.05)
        archiver.start()
        self.assertTrue(archiver.running)
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline and not bundle.exists():
                time.sleep(0.02)
            self.assertTrue(bundle.exists())
        finally:
            archiver.close()

        self.assertFalse(archiver.running)
        self.assertFalse((self.tmp / "app.log.1").exists())

    def test_close_is_idempotent(self) -> None:
        source = RotatingFile(self.tmp / "app.log")
        self.addCleanup(source.close)
        archiver = Archiver([source], after=60.0, check_interval=0.05)
        archiver.start()
        archiver.close()
        archiver.close()
        self.assertFalse(archiver.running)

    def test_start_is_idempotent(self) -> None:
        source = RotatingFile(self.tmp / "app.log")
        self.addCleanup(source.close)
        archiver = Archiver([source], after=60.0, check_interval=0.05)
        archiver.start()
        thread = archiver._thread  # noqa: SLF001 - checking the single thread
        archiver.start()
        self.assertIs(archiver._thread, thread)  # noqa: SLF001
        archiver.close()

    def test_sweep_without_start_works(self) -> None:
        source = RotatingFile(self.tmp / "app.log", max_bytes=None)
        self.addCleanup(source.close)
        source.write("payload\n")
        source.flush()
        source.rotate()
        source.close()
        set_mtime(self.tmp / "app.log.1", time.time() - 3600)

        archiver = Archiver([source], after=60.0)
        self.assertFalse(archiver.running)
        self.assertEqual(archiver.sweep(), 1)


if __name__ == "__main__":
    unittest.main()
