"""time based rotation: relative interval=, wall clock at=, and parsing."""

from __future__ import annotations

import unittest
from datetime import datetime, time as time_of_day

from toomar.rotation import (
    RotatingFile,
    next_roll_deadline,
    parse_interval,
    parse_time_of_day,
)

from helpers import TmpDirTestCase


class FakeClock:
    """a settable monotonic clock."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ParseIntervalTests(unittest.TestCase):

    def test_bare_numbers_are_seconds(self) -> None:
        self.assertEqual(parse_interval(90), 90.0)
        self.assertEqual(parse_interval(90.5), 90.5)
        self.assertEqual(parse_interval("90"), 90.0)

    def test_units(self) -> None:
        self.assertEqual(parse_interval("45s"), 45.0)
        self.assertEqual(parse_interval("30m"), 1800.0)
        self.assertEqual(parse_interval("1h"), 3600.0)
        self.assertEqual(parse_interval("2d"), 172800.0)
        self.assertEqual(parse_interval("1w"), 604800.0)

    def test_case_and_space_insensitive(self) -> None:
        self.assertEqual(parse_interval("1H"), 3600.0)
        self.assertEqual(parse_interval(" 1 h "), 3600.0)

    def test_sums(self) -> None:
        self.assertEqual(parse_interval("1h30m"), 5400.0)
        self.assertEqual(parse_interval("1d2h3m4s"), 93784.0)

    def test_rejects_garbage(self) -> None:
        for bad in ("", "banana", "1x", "-5", "1h banana", "0", "h"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_interval(bad)

    def test_rejects_wrong_types(self) -> None:
        with self.assertRaises(TypeError):
            parse_interval(True)
        with self.assertRaises(TypeError):
            parse_interval(None)


class ParseTimeOfDayTests(unittest.TestCase):

    def test_strings(self) -> None:
        self.assertEqual(parse_time_of_day("00:00"), time_of_day(0, 0))
        self.assertEqual(parse_time_of_day("9:05"), time_of_day(9, 5))
        self.assertEqual(parse_time_of_day("23:59:59"), time_of_day(23, 59, 59))

    def test_time_passthrough(self) -> None:
        moment = time_of_day(6, 30)
        self.assertIs(parse_time_of_day(moment), moment)

    def test_rejects_invalid(self) -> None:
        for bad in ("25:00", "12:60", "noon", "12", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_time_of_day(bad)
        with self.assertRaises(TypeError):
            parse_time_of_day(600)


class NextRollDeadlineTests(unittest.TestCase):

    def test_interval_is_relative_to_now(self) -> None:
        clock = FakeClock(100.0)
        self.assertEqual(next_roll_deadline("1h", clock=clock), 3700.0)

    def test_at_is_the_next_wall_clock_boundary(self) -> None:
        clock = FakeClock(0.0)
        wall = datetime(2026, 9, 25, 22, 30).timestamp()
        deadline = next_roll_deadline(at="00:00", clock=clock, wall_clock=lambda: wall)

        self.assertEqual(deadline, 1.5 * 3600.0)  # 1h30m until midnight

    def test_at_today_when_the_boundary_has_not_passed(self) -> None:
        clock = FakeClock(0.0)
        wall = datetime(2026, 9, 25, 1, 0).timestamp()
        deadline = next_roll_deadline(at="02:00", clock=clock, wall_clock=lambda: wall)

        self.assertEqual(deadline, 3600.0)

    def test_both_none_disables_time_rotation(self) -> None:
        self.assertIsNone(next_roll_deadline())

    def test_interval_and_at_together_raise(self) -> None:
        with self.assertRaises(ValueError):
            next_roll_deadline(interval="1h", at="00:00")


class IntervalRotationTests(TmpDirTestCase):

    def test_rolls_once_the_interval_elapses(self) -> None:
        clock = FakeClock()
        sink = RotatingFile(
            self.tmp / "app.log",
            interval="1h",
            clock=clock,
            backup_count=3,
        )

        sink.write("a\n")
        clock.advance(30 * 60)
        sink.write("b\n")
        sink.flush()
        self.assertEqual((self.tmp / "app.log").read_text(), "a\nb\n")

        clock.advance(31 * 60)  # past the 1h deadline
        sink.write("c\n")
        sink.close()

        self.assertEqual((self.tmp / "app.log.1").read_text(), "a\nb\n")
        self.assertEqual((self.tmp / "app.log").read_text(), "c\n")

    def test_the_deadline_is_recomputed_after_each_roll(self) -> None:
        clock = FakeClock()
        sink = RotatingFile(self.tmp / "app.log", interval="10s", clock=clock)

        for index in range(3):
            sink.write(f"{index}\n")
            clock.advance(11)

        sink.close()
        self.assertEqual((self.tmp / "app.log.1").read_text(), "1\n")
        self.assertEqual((self.tmp / "app.log.2").read_text(), "0\n")

    def test_rotation_fires_only_once_per_write(self) -> None:
        clock = FakeClock()
        sink = RotatingFile(self.tmp / "app.log", interval="1s", clock=clock)

        sink.write("a\n")
        clock.advance(100)
        sink.write("b\n")
        sink.close()

        self.assertEqual((self.tmp / "app.log.1").read_text(), "a\n")
        self.assertEqual((self.tmp / "app.log").read_text(), "b\n")

    def test_no_time_roll_without_interval_or_at(self) -> None:
        clock = FakeClock()
        sink = RotatingFile(self.tmp / "app.log", max_bytes=None, clock=clock)
        sink.write("a\n")
        clock.advance(10_000)
        sink.write("b\n")
        sink.close()

        self.assertEqual((self.tmp / "app.log").read_text(), "a\nb\n")


class WallClockRotationTests(TmpDirTestCase):

    def test_rolls_after_the_named_time_of_day(self) -> None:
        clock = FakeClock()
        midnight = datetime(2026, 9, 25, 0, 0).timestamp()
        wall = [midnight - 60]  # one minute before midnight

        sink = RotatingFile(
            self.tmp / "app.log",
            at="00:00",
            clock=clock,
            wall_clock=lambda: wall[0],
        )

        sink.write("before\n")
        wall[0] = midnight + 30  # past the boundary
        clock.advance(90)
        sink.write("after\n")
        sink.close()

        self.assertEqual((self.tmp / "app.log.1").read_text(), "before\n")
        self.assertEqual((self.tmp / "app.log").read_text(), "after\n")

    def test_datetime_time_is_accepted(self) -> None:
        clock = FakeClock()
        wall = [datetime(2026, 9, 25, 0, 0).timestamp() + 7_000]
        sink = RotatingFile(
            self.tmp / "app.log",
            at=time_of_day(2, 0),
            clock=clock,
            wall_clock=lambda: wall[0],
        )
        sink.write("a\n")
        clock.advance(3 * 3600)
        sink.write("b\n")
        sink.close()

        self.assertEqual((self.tmp / "app.log.1").read_text(), "a\n")


class MutuallyExclusiveTests(TmpDirTestCase):

    def test_interval_and_at_together_raise(self) -> None:
        with self.assertRaises(ValueError):
            RotatingFile(self.tmp / "app.log", interval="1h", at="00:00")
        with self.assertRaises(ValueError):
            RotatingFile(self.tmp / "app.log", interval=60, at="12:00")


if __name__ == "__main__":
    unittest.main()
