"""Client minimal de l'API locale « Live Client Data » de League of Legends (port 2999)."""

from __future__ import annotations

import requests
import urllib3

# Le client de jeu utilise un certificat auto-signé sur 127.0.0.1 : on désactive l'alerte,
# et uniquement pour cette connexion locale.
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
        """Riot ID du joueur local (« Pseudo#TAG »)."""
        return self._get("activeplayername")
