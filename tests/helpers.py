"""helpers shared by the test modules."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from toomar.logger import ERROR, INFO, WARNING, Log


class TmpDirTestCase(unittest.TestCase):
    """a test case with a throwaway directory under ``self.tmp``."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="toomar-test-"))
        self.tmp = self._tmp
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)


def make_log(
    message: str = "hello",
    level: str = INFO,
    name: str = "app",
) -> Log:
    return Log(message=message, level=level, logger_name=name)


def make_logs(*specs: tuple[str, str]) -> list[Log]:
    """``make_logs(("a", INFO), ("b", ERROR))`` -> a two record batch."""
    return [make_log(message, level) for message, level in specs]


def generations(directory: Path, stem: str = "app.log") -> list[str]:
    """sorted names of the numbered generations of ``stem`` in ``directory``."""
    prefix = stem + "."
    return sorted(
        p.name
        for p in directory.iterdir()
        if p.name.startswith(prefix)
        and p.name[len(prefix) :].isdigit()
    )


def names(directory: Path) -> list[str]:
    """sorted names of everything in ``directory``."""
    return sorted(p.name for p in directory.iterdir())


__all__ = [
    "ERROR",
    "INFO",
    "WARNING",
    "Log",
    "TmpDirTestCase",
    "generations",
    "make_log",
    "make_logs",
    "names",
]
