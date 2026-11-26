"""Chemins et commande de lancement, valables en mode source ET en exécutable (PyInstaller)."""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

# Dossier « de travail » : celui de l'exécutable, ou la racine du projet en mode source.
# config.toml, logs/ et data/ y sont lus / écrits.
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent


def cli(*args: str) -> list[str]:
    """Commande pour relancer ce programme avec une sous-commande (ex. cli("director", "-v"))."""
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, "-m", "lolauto", *args]
