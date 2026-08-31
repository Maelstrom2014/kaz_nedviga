"""Central logging setup: rotating files under logs/ + console stream.

Shared by app.py (the "app" logger) and parsers/base.py (the "parsers" and
"parsers.phone" loggers) so every file handler rotates instead of growing
without bound and all logs live in one place.
"""
import logging
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


def errors_file_handler(filename: str = "parsers_errors.log") -> RotatingFileHandler:
    """Rotating error-file handler shared by the "app" and "parsers" loggers."""
    h = RotatingFileHandler(
        str(LOGS_DIR / filename), maxBytes=MAX_BYTES, backupCount=BACKUPS,
        encoding="utf-8")
    h.setLevel(logging.DEBUG)
    h.setFormatter(FULL_FMT)
    return h


def phone_file_handler() -> RotatingFileHandler:
    """Rotating handler for the per-listing phone extraction log."""
    h = RotatingFileHandler(
        str(LOGS_DIR / "phone_extraction.log"), maxBytes=MAX_BYTES,
        backupCount=BACKUPS, encoding="utf-8")
    h.setLevel(logging.DEBUG)
    h.setFormatter(PHONE_FMT)
    return h


def console_handler(level: int = logging.DEBUG) -> logging.StreamHandler:
    h = logging.StreamHandler()
    h.setLevel(level)
    h.setFormatter(FULL_FMT)
    return h
