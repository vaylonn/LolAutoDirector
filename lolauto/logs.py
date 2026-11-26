"""Shared logging setup: console (UTF-8, even on Windows) plus an optional file."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup(verbose: bool = False, log_file: str | None = None, fmt: str = "%(asctime)s %(message)s") -> None:
    if hasattr(sys.stdout, "reconfigure"):  # Windows: the console defaults to cp1252, which breaks non-ASCII characters
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, mode="a", encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format=fmt, datefmt="%H:%M:%S",
                        handlers=handlers, force=True)
    # obsws-python logs its connection parameters (password included) and tracebacks: silence it
    logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
