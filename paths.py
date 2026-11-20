"""Paths of the app (standard library only): logs next to the code, with a fallback in LOCALAPPDATA."""
from __future__ import annotations

import os
import tempfile

APP_NAME = "VirtualCaptain"


def base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test_%d" % os.getpid())
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


def log_dir() -> str:
    """<base>/logs if writable, otherwise %LOCALAPPDATA%\\VirtualCaptain\\logs (or the temp folder)."""
    primary = os.path.join(base_dir(), "logs")
    if _writable(primary):
        return primary
    fallback = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(), APP_NAME, "logs")
    try:
        os.makedirs(fallback, exist_ok=True)
    except OSError:
        pass  # path returned anyway: applog ignores file errors and keeps logging to the GUI queue
    return fallback
