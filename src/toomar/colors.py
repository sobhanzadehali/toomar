"""
minimal, dependency free ANSI styling helpers for console output.
"""

import os
import sys
from typing import TextIO

RESET = "\x1b[0m"

_CODES: dict[str, str] = {
    "black": "\x1b[30m",
    "red": "\x1b[31m",
    "green": "\x1b[32m",
    "yellow": "\x1b[33m",
    "blue": "\x1b[34m",
    "magenta": "\x1b[35m",
    "cyan": "\x1b[36m",
    "white": "\x1b[37m",
    "gray": "\x1b[90m",
    "grey": "\x1b[90m",
    "bright_red": "\x1b[91m",
    "bright_green": "\x1b[92m",
    "bright_yellow": "\x1b[93m",
    "bright_blue": "\x1b[94m",
    "bright_magenta": "\x1b[95m",
    "bright_cyan": "\x1b[96m",
    "bright_white": "\x1b[97m",
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "italic": "\x1b[3m",
    "underline": "\x1b[4m",
}

def fg(name: str) -> str:
    """named foreground color, e.g. ``fg("red")``."""
    code = _CODES.get(name)
    if code is None:
        raise KeyError(f"unknown color {name!r}")
    return code


def fg256(index: int) -> str:
    """foreground from the 256 color palette."""
    if not 0 <= index <= 255:
        raise ValueError(f"palette index must be in 0..255, got {index}")
    return f"\x1b[38;5;{index}m"


def rgb(r: int, g: int, b: int) -> str:
    """truecolor foreground."""
    for value in (r, g, b):
        if not 0 <= value <= 255:
            raise ValueError(f"rgb channels must be in 0..255, got {value}")
    return f"\x1b[38;2;{r};{g};{b}m"


def style(text: str, *names: str) -> str:
    """wrap ``text`` in the given named styles, e.g. ``style("x", "bold", "red")``."""
    prefix = "".join(fg(name) for name in names)
    return f"{prefix}{text}{RESET}"


_env_allows_color: bool | None = None


def _env_supports_color() -> bool:
    # NO_COLOR and TERM=dumb are process wide facts, so resolve them once.
    global _env_allows_color
    if _env_allows_color is None:
        if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
            _env_allows_color = False
        elif sys.platform == "win32" and not os.environ.get("WT_SESSION") \
                and not os.environ.get("TERM_PROGRAM"):
            # classic windows console without virtual terminal processing
            _env_allows_color = False
        else:
            _env_allows_color = True
    return _env_allows_color


def supports_color(stream: TextIO | None = None) -> bool:
    """True when escape sequences in ``stream`` will actually be rendered."""
    if stream is None:
        stream = sys.stdout
    if not _env_supports_color():
        return False
    isatty = getattr(stream, "isatty", None)
    if isatty is None or not isatty():
        return False
    encoding = getattr(stream, "encoding", "") or ""
    return "utf" in encoding.lower()
