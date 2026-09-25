from abc import ABC, abstractmethod

from toomar.logger import Log


class BaseSink(ABC):

    @abstractmethod
    def flush(self, logs: list[Log]):
        pass