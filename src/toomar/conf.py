from dataclasses import dataclass

from toomar.sinks import BaseSink


@dataclass
class Config:
    sinks: list[BaseSink]
    format:str|None

