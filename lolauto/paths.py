"""Paths and relaunch command, valid both from source and as a PyInstaller executable."""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

# Working folder: the executable's folder, or the project root when running from source.
# config.toml, logs/ and data/ are read and written there.
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent


def cli(*args: str) -> list[str]:
    """Command that relaunches this program with a sub-command (e.g. cli("director", "-v"))."""
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, "-m", "lolauto", *args]
