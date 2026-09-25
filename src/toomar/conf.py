from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from toomar.sinks import BaseSink


@dataclass
class Config:
    sinks: list[BaseSink]
    format: str | None
