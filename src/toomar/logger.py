from datetime import datetime

from toomar.conf import Config


class Log:
    def __init__(self, message: str):
        self.message: str = message
        self.created_date = datetime.now()  # noqa: DTZ005

    def __str__(self):
        return self.message


class Logger:
    """
    the interface  that user works with to log data
    """
    def __init__(self, conf:Config):
        self.conf = conf

    def info(self, *args):
        pass

    def debug(self, *args):
        pass

    def error(self, *args):
        pass

    def warning(self, *args):
        pass
