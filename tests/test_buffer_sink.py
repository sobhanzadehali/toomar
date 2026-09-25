"""BufferSink: in memory capture, the BufferSink/ConsoleSink stand-in for tests."""

from __future__ import annotations

import unittest

from toomar.colors import RESET, fg
from toomar.logger import ERROR, INFO
from toomar.sinks import BufferSink

from helpers import make_log, make_logs


class BufferSinkTests(unittest.TestCase):

    def test_clear_empties_the_buffer(self) -> None:
        sink = BufferSink()
        sink.flush(make_logs(("a", INFO), ("b", INFO)))
        self.assertEqual(len(sink), 4)

        sink.clear()
        self.assertEqual(sink.getvalue(), "")
        self.assertEqual(len(sink), 0)

        # and the buffer is reusable afterwards
        sink.flush(make_logs(("c", INFO)))
        self.assertEqual(sink.getvalue(), "c\n")

    def test_one_chunk_per_batch(self) -> None:
        sink = BufferSink()
        sink.flush(make_logs(("a", INFO), ("b", INFO), ("c", INFO)))
        sink.flush(make_logs(("d", INFO)))

        self.assertEqual(sink.getvalue(), "a\nb\nc\nd\n")
        self.assertEqual(len(sink), 8)

    def test_empty_batch_is_a_noop(self) -> None:
        sink = BufferSink()
        sink.flush([])
        self.assertEqual(sink.getvalue(), "")
        self.assertEqual(len(sink), 0)
    def test_colors_off_by_default(self) -> None:
        sink = BufferSink()
        sink.flush(make_logs(("boom", ERROR)))
        self.assertNotIn("\x1b[", sink.getvalue())
        self.assertEqual(sink.getvalue(), "boom\n")

    def test_colors_on(self) -> None:
        sink = BufferSink(colors=True)
        sink.flush(make_logs(("a", INFO), ("b", ERROR)))

        self.assertEqual(
            sink.getvalue(),
            f"{fg('green')}a{RESET}\n{fg('red')}b{RESET}\n",
        )

    def test_custom_color_map(self) -> None:
        sink = BufferSink(colors=True, color_map={INFO: "cyan"})
        sink.flush(make_logs(("a", INFO), ("b", ERROR)))

        self.assertEqual(sink.getvalue(), f"{fg('cyan')}a{RESET}\nb\n")

    def test_custom_terminator(self) -> None:
        sink = BufferSink(terminator="|")
        sink.flush(make_logs(("a", INFO), ("b", INFO)))
        self.assertEqual(sink.getvalue(), "a|b|")

    def test_close_is_a_noop_and_leaves_the_buffer_readable(self) -> None:
        sink = BufferSink()
        sink.flush([make_log("a", INFO)])
        sink.close()
        self.assertEqual(sink.getvalue(), "a\n")


if __name__ == "__main__":
    unittest.main()
