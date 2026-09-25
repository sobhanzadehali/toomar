from datetime import datetime

from toomar.logger import Log


class LogFormatter:
    @staticmethod
    def format(log: Log, fstr: str):
        pass

    @staticmethod
    def is_valid_format(fstr: str):
        pass



class DateTimeFormatter:

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