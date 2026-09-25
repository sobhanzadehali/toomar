"""ConsoleSink writes to stdout only, and resolves it on every flush."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from toomar.colors import RESET, fg
from toomar.logger import ERROR, INFO
from toomar.sinks import ConsoleSink

from helpers import make_log, make_logs


class CapturingStream(io.StringIO):
    """a StringIO that counts ``write`` calls, so we can assert one per batch."""

    def __init__(self) -> None:
        super().__init__()
        self.writes = 0
        self.flushes = 0

    def write(self, data: str) -> int:
        self.writes += 1
        return super().write(data)

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


class ConsoleSinkTests(unittest.TestCase):

    def test_writes_to_redirected_stdout(self) -> None:
        stream = CapturingStream()
        sink = ConsoleSink(colors=False)
        with redirect_stdout(stream):
            sink.flush(make_logs(("one", INFO), ("two", ERROR)))

        self.assertEqual(stream.getvalue(), "one\ntwo\n")

    def test_follows_stdout_swapped_between_flushes(self) -> None:
        sink = ConsoleSink(colors=False)
        first, second = CapturingStream(), CapturingStream()
        with redirect_stdout(first):
            sink.flush(make_logs(("a", INFO)))
        with redirect_stdout(second):
            sink.flush(make_logs(("b", INFO)))

        self.assertEqual(first.getvalue(), "a\n")
        self.assertEqual(second.getvalue(), "b\n")

    def test_empty_batch_is_a_noop(self) -> None:
        stream = CapturingStream()
        with redirect_stdout(stream):
            ConsoleSink().flush([])

        self.assertEqual(stream.getvalue(), "")
        self.assertEqual(stream.writes, 0)

    def test_batch_is_one_write_and_one_flush(self) -> None:
        stream = CapturingStream()
        sink = ConsoleSink(colors=False)
        with redirect_stdout(stream):
            sink.flush(make_logs(("a", INFO), ("b", INFO), ("c", INFO)))

        self.assertEqual(stream.writes, 1)
        self.assertEqual(stream.flushes, 1)
        self.assertEqual(stream.getvalue(), "a\nb\nc\n")

    def test_autoflush_false_skips_the_flush_syscall(self) -> None:
        stream = CapturingStream()
        with redirect_stdout(stream):
            ConsoleSink(colors=False, autoflush=False).flush(make_logs(("a", INFO)))

        self.assertEqual(stream.flushes, 0)

    def test_no_escapes_when_not_a_tty(self) -> None:
        stream = CapturingStream()  # StringIO.isatty() is False
        with redirect_stdout(stream):
            ConsoleSink().flush(make_logs(("a", INFO), ("b", ERROR)))

        value = stream.getvalue()
        self.assertNotIn("\x1b[", value)
        self.assertEqual(value, "a\nb\n")

    def test_escapes_on_a_capable_terminal(self) -> None:
        class Tty(CapturingStream):
            encoding = "utf-8"

            def isatty(self) -> bool:
                return True

        stream = Tty()
        with redirect_stdout(stream):
            ConsoleSink().flush(make_logs(("a", INFO)))

        self.assertEqual(stream.getvalue(), f"{fg('green')}a{RESET}\n")

    def test_escapes_present_when_forced(self) -> None:
        stream = CapturingStream()
        with redirect_stdout(stream):
            ConsoleSink(colors=True).flush(make_logs(("boom", ERROR)))

        self.assertEqual(
            stream.getvalue(),
            f"{fg('red')}boom{RESET}\n",
        )

    def test_colors_false_never_emits_escapes_on_a_fake_tty(self) -> None:
        class Tty(CapturingStream):
            encoding = "utf-8"

            def isatty(self) -> bool:
                return True

        stream = Tty()
        with redirect_stdout(stream):
            ConsoleSink(colors=False).flush(make_logs(("a", INFO)))

        self.assertEqual(stream.getvalue(), "a\n")

    def test_custom_terminator(self) -> None:
        stream = CapturingStream()
        with redirect_stdout(stream):
            ConsoleSink(colors=False, terminator="; ").flush(
                make_logs(("a", INFO), ("b", INFO))
            )

        self.assertEqual(stream.getvalue(), "a; b; ")

    def test_custom_color_map(self) -> None:
        stream = CapturingStream()
        with redirect_stdout(stream):
            ConsoleSink(colors=True, color_map={INFO: "blue"}).flush(
                make_logs(("a", INFO), ("b", ERROR))
            )

        # INFO is mapped, ERROR is absent from the map and stays bare
        self.assertEqual(stream.getvalue(), f"{fg('blue')}a{RESET}\nb\n")

    def test_constructor_takes_no_stream_argument(self) -> None:
        with self.assertRaises(TypeError):
            ConsoleSink(io.StringIO())  # type: ignore[misc]

    def test_write_after_close_still_works(self) -> None:
        stream = CapturingStream()
        sink = ConsoleSink(colors=False)
        sink.close()  # BaseSink.close is a no-op for the console
        with redirect_stdout(stream):
            sink.flush([make_log("still here", INFO)])

        self.assertEqual(stream.getvalue(), "still here\n")


if __name__ == "__main__":
    unittest.main()
