"""Central logging setup: rotating files under logs/ + console stream.

Shared by app.py (the "app" logger) and parsers/base.py (the "parsers" and
"parsers.phone" loggers) so every file handler rotates instead of growing
without bound and all logs live in one place.
"""
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

FULL_FMT = logging.Formatter(
    "%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
PHONE_FMT = logging.Formatter(
    "%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)

MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file, 3 rotated backups
BACKUPS = 3


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that survives rollover while the file is open elsewhere.

    On Windows ``os.rename`` during rollover raises PermissionError
    (WinError 32) when another process (editor, tail, backup tool) or a
    second handler holds the file. Retry briefly; if it stays locked, keep
    appending to the current file and retry on the next emit instead of
    raising out of ``emit()``.
    """

    _RETRIES = 5
    _DELAY = 0.2  # seconds

    def doRollover(self) -> None:
        for attempt in range(self._RETRIES):
            try:
                super().doRollover()
                return
            except PermissionError:
                if attempt == self._RETRIES - 1:
                    break
                time.sleep(self._DELAY)
        # Still locked: skip rotation this time. The failed rollover closed
        # the stream, so reopen the current file to keep logging working.
        if self.stream is None or self.stream.closed:
            self.stream = self._open()


def errors_file_handler(filename: str = "parsers_errors.log") -> RotatingFileHandler:
    """Rotating error-file handler shared by the "app" and "parsers" loggers.

    Returns the same instance on every call: two handlers opening the same
    file would keep a second handle open and break rollover on Windows.
    """
    global _shared_errors_handler
    if _shared_errors_handler is None:
        h = SafeRotatingFileHandler(
            str(LOGS_DIR / filename), maxBytes=MAX_BYTES, backupCount=BACKUPS,
            encoding="utf-8")
        h.setLevel(logging.DEBUG)
        h.setFormatter(FULL_FMT)
        _shared_errors_handler = h
    return _shared_errors_handler


_shared_errors_handler: SafeRotatingFileHandler | None = None


def phone_file_handler() -> RotatingFileHandler:
    """Rotating handler for the per-listing phone extraction log."""
    h = SafeRotatingFileHandler(
        str(LOGS_DIR / "phone_extraction.log"), maxBytes=MAX_BYTES,
        backupCount=BACKUPS, encoding="utf-8")
    h.setLevel(logging.DEBUG)
    h.setFormatter(PHONE_FMT)
    return h


def bot_file_handler() -> RotatingFileHandler:
    """Rotating handler for the telegram-bot log (logs/telegram_bot.log).

    The "bot" logger writes here only: search activity, user registrations,
    subscription requests, quota denials, support messages. Not shared with
    "app"/"parsers" (a dedicated file keeps bot traffic out of server logs).
    """
    h = SafeRotatingFileHandler(
        str(LOGS_DIR / "telegram_bot.log"), maxBytes=MAX_BYTES,
        backupCount=BACKUPS, encoding="utf-8")
    h.setLevel(logging.DEBUG)
    h.setFormatter(FULL_FMT)
    return h


def console_handler(level: int = logging.DEBUG) -> logging.StreamHandler:
    h = logging.StreamHandler()
    h.setLevel(level)
    h.setFormatter(FULL_FMT)
    return h
