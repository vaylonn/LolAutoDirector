"""Minimal client for League of Legends' local Live Client Data API (port 2999)."""

from __future__ import annotations

import requests
import urllib3

# The game client serves a self-signed certificate on 127.0.0.1: silence the warning
# (verification is only disabled for this local connection).
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class RiotClient:
    def __init__(self, base_url: str = "https://127.0.0.1:2999", timeout: float = 1.0):
        self.base = base_url.rstrip("/") + "/liveclientdata"
        self.timeout = timeout
        self._s = requests.Session()
        self._s.verify = False

    def _get(self, path: str):
        r = self._s.get(f"{self.base}/{path}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def all_game_data(self) -> dict:
        return self._get("allgamedata")

    def active_name(self) -> str:
        """Riot ID of the local player ("Name#TAG")."""
        return self._get("activeplayername")
