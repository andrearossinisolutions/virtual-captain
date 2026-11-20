"""Logging (standard library only): rotating file, optional console, queue for the GUI, stdout/stderr redirect.

Threads never touch tkinter: the GUI drains the queue of (level, text) from its own after() loop.
The login code is never passed to the logger (the window shows it directly; see main.App.show_login).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import queue
import sys

LOGGER_NAME = "captain"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOG_FILE = "captain.log"
GUI_QUEUE_SIZE = 2000


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)


class GuiQueueHandler(logging.Handler):
    """Puts (levelno, text) in a queue.Queue; if full it drops (never blocks a thread)."""

    def __init__(self, q: "queue.Queue") -> None:
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait((record.levelno, self.format(record)))
        except Exception:  # noqa: BLE001
            pass


def add_file_handler(logger: logging.Logger, log_dir: str):
    """Rotating file (~1 MB x 3), added once. Returns the path or None."""
    for h in logger.handlers:
        if getattr(h, "vc_file", False):
            return getattr(h, "baseFilename", None)
    try:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, LOG_FILE)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter(LOG_FORMAT))
        fh.vc_file = True  # type: ignore[attr-defined]
        logger.addHandler(fh)
        return path
    except OSError:
        return None


def setup_logging(log_dir: str, console: bool = False, debug: bool = False, gui_queue=None) -> logging.Logger:
    """Idempotent: can be called by the launcher and by the app without duplicating handlers."""
    logger = get_logger()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    add_file_handler(logger, log_dir)
    if console and sys.stderr is not None and not any(getattr(h, "vc_console", False) for h in logger.handlers):
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter(LOG_FORMAT))
        ch.vc_console = True  # type: ignore[attr-defined]
        logger.addHandler(ch)
    if gui_queue is not None and not any(isinstance(h, GuiQueueHandler) for h in logger.handlers):
        qh = GuiQueueHandler(gui_queue)
        qh.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        logger.addHandler(qh)
    return logger


class LoggerWriter:
    """File-like object that sends complete lines to the logger (under pythonw stdout/stderr are None)."""

    def __init__(self, logger: logging.Logger, level: int) -> None:
        self.logger, self.level, self._buf = logger, level, ""

    def write(self, text) -> int:
        self._buf += str(text)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.logger.log(self.level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buf.strip():
            self.logger.log(self.level, self._buf.rstrip())
        self._buf = ""

    def isatty(self) -> bool:
        return False


def redirect_stdio(logger: logging.Logger) -> None:
    """From now on print() and tracebacks written to stdout/stderr end up in the log."""
    sys.stdout = LoggerWriter(logger, logging.INFO)  # type: ignore[assignment]
    sys.stderr = LoggerWriter(logger, logging.WARNING)  # type: ignore[assignment]
