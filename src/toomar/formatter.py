"""
format string compilation and rendering for file backed sinks.

a format string is a plain ``str.format`` template over four log fields::

    {asctime} {levelname} {name} {message}

* ``{asctime}``  — the log's ``created_date``, rendered with ``%Y-%m-%d %H:%M:%S``
  by default, or with a custom ``strftime`` pattern as ``{asctime:%H:%M}``
* ``{levelname}`` — ``"INFO"``, ``"ERROR"``, ...
* ``{name}``     — the logger name
* ``{message}``  — the message, without any decoration

templates are compiled once per distinct string and cached, so the hot path is a
list walk and a ``str.join`` instead of a regular expression scan per log. an
unknown field name raises :class:`ValueError` **while compiling**, which means a
misconfigured format fails loudly at sink construction time rather than silently
dropping fields on the first log line.
"""

from __future__ import annotations

import string
from datetime import datetime
from typing import Any

from toomar.logger import Log

#: the format file sinks use unless the caller passes something else
DEFAULT_FORMAT = "{asctime} {levelname} {name} {message}"

#: ``strftime`` pattern behind ``{asctime}`` when no override is given
DEFAULT_ASCTIME = "%Y-%m-%d %H:%M:%S"

#: every field name a format string may reference
FIELDS: frozenset[str] = frozenset({"asctime", "levelname", "name", "message"})

_PARSER = string.Formatter()

# a compiled format is ``(asctime_pattern, parts)`` where each part is either a
# plain literal or a ``(kind, field, spec)`` field reference. built once, then
# reused for every log line.
_compiled_cache: dict[str, tuple[str, tuple[tuple[Any, ...], ...]]] = {}


class DateTimeFormatter:
    """``strftime`` wrapper with validation of the pattern."""

    @staticmethod
    def is_valid_format(fstr: str) -> bool:
        try:
            datetime(2000, 2, 2).strftime(fstr)
            return True
        except ValueError:
            return False

    @staticmethod
    def format(dt: datetime, fstr: str) -> str:
        if not DateTimeFormatter.is_valid_format(fstr):
            raise ValueError(f"Invalid format string: {fstr!r}")
        return dt.strftime(fstr)


def compile_format(fstr: str) -> tuple[str, tuple[tuple[Any, ...], ...]]:
    """compile ``fstr`` into a reusable template.

    raises :class:`ValueError` on malformed braces, unknown field names or an
    invalid ``{asctime:...}`` pattern. the result is cached, so calling this on
    the same string twice is cheap.
    """
    cached = _compiled_cache.get(fstr)
    if cached is not None:
        return cached

    if not isinstance(fstr, str):
        raise TypeError(f"format must be a string, got {type(fstr).__name__}")

    try:
        chunks = list(_PARSER.parse(fstr))
    except ValueError as exc:
        raise ValueError(f"invalid format string {fstr!r}: {exc}") from exc

    asctime_pattern = DEFAULT_ASCTIME
    parts: list[tuple[Any, ...]] = []

    for literal, field, spec, conversion in chunks:
        if literal:
            parts.append((0, literal))
        if field is None:
            continue
        if field not in FIELDS:
            raise ValueError(
                f"unknown field {field!r} in format {fstr!r}; "
                f"expected one of {', '.join(sorted(FIELDS))}"
            )
        if conversion not in (None, "s"):
            raise ValueError(
                f"unsupported conversion {conversion!r} on field {field!r} in format {fstr!r}"
            )
        if field == "asctime":
            if spec:
                if not DateTimeFormatter.is_valid_format(spec):
                    raise ValueError(
                        f"invalid {{asctime}} pattern {spec!r} in format {fstr!r}"
                    )
                asctime_pattern = spec
            parts.append((1, "asctime", ""))
        else:
            parts.append((1, field, spec or ""))

    compiled = (asctime_pattern, tuple(parts))
    _compiled_cache[fstr] = compiled
    return compiled


def check_format(fstr: str) -> bool:
    """True when ``fstr`` compiles. never raises."""
    try:
        compile_format(fstr)
    except (ValueError, TypeError):
        return False
    return True


def format_log(log: Log, fstr: str | None) -> str:
    """render a single :class:`~toomar.logger.Log` with ``fstr``.

    ``fstr=None`` yields the bare message, which is the right choice for sinks
    that decorate lines themselves.
    """
    if fstr is None:
        return log.message

    asctime_pattern, parts = compile_format(fstr)

    out: list[str] = []
    append = out.append
    for part in parts:
        if part[0] == 0:
            append(part[1])
            continue
        field = part[1]
        if field == "asctime":
            append(log.created_date.strftime(asctime_pattern))
            continue
        if field == "levelname":
            value = log.level
        elif field == "name":
            value = log.logger_name
        else:
            value = log.message
        spec = part[2]
        append(format(value, spec) if spec else value)

    return "".join(out)


class LogFormatter:
    """compatibility shim over :func:`format_log`."""

    @staticmethod
    def format(log: Log, fstr: str | None) -> str:
        return format_log(log, fstr)

    @staticmethod
    def is_valid_format(fstr: str) -> bool:
        return check_format(fstr)

    @staticmethod
    def compile(fstr: str):
        return compile_format(fstr)
