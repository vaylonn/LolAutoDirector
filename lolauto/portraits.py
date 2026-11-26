"""Portraits officiels des champions (Data Dragon de Riot), téléchargés une fois puis gardés en cache.

    https://ddragon.leagueoflegends.com/cdn/<version>/img/champion/<Id>.png   (120×120)

Les identifiants viennent de l'API locale du jeu : `rawChampionName` = "game_character_displayname_<Id>".
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import requests

log = logging.getLogger("portraits")

DDRAGON = "https://ddragon.leagueoflegends.com"
from .paths import BASE as ROOT
_VALID_ID = re.compile(r"^[A-Za-z0-9]{1,32}$")


def valid_ids(ids) -> list[str]:
    """Ne garde que des identifiants de champion plausibles (lettres/chiffres).

    Les noms arrivent par le réseau (paquets UDP des agents) et servent à construire une URL et un
    nom de fichier : on refuse tout le reste (« ../ », espaces, etc.).
    """
    if not isinstance(ids, (list, tuple)):
        return []
    return sorted({i for i in ids if isinstance(i, str) and _VALID_ID.match(i)})


def champion_id(player: dict) -> str:
    """Identifiant Data Dragon d'un joueur de l'API (« MonkeyKing » pour Wukong, etc.)."""
    raw = player.get("rawChampionName") or ""
    if raw.startswith("game_character_displayname_"):
        return raw.split("game_character_displayname_", 1)[1]
    return (player.get("championName") or "").replace(" ", "").replace("'", "").replace(".", "")


def enemy_ids(all_players: list, me: str, same_name) -> list[str]:
    """Champions de l'équipe adverse, vus depuis le joueur `me`."""
    team = next((p.get("team") for p in all_players
                 if any(same_name(n, me) for n in (p.get("riotIdGameName"), p.get("summonerName")) if n)), None)
    if not team:
        return []
    return [cid for p in all_players if p.get("team") and p.get("team") != team and (cid := champion_id(p))]


def ensure_portraits(ids, cache_dir: str | Path = "data/portraits", timeout: float = 5.0) -> dict[str, Path]:
    """Télécharge les portraits manquants. Renvoie {id: chemin} pour ceux disponibles."""
    cache = Path(cache_dir)
    if not cache.is_absolute():
        cache = ROOT / cache
    cache.mkdir(parents=True, exist_ok=True)
    out, missing = {}, []
    for cid in valid_ids(list(ids)):
        p = cache / f"{cid}.png"
        if p.exists() and p.stat().st_size > 0:
            out[cid] = p
        else:
            missing.append(cid)
    if not missing:
        return out
    try:
        version = requests.get(f"{DDRAGON}/api/versions.json", timeout=timeout).json()[0]
    except Exception as e:
        log.warning("Portraits : Data Dragon injoignable (%s) — vision sans reconnaissance des champions", e)
        return out
    for cid in missing:
        try:
            r = requests.get(f"{DDRAGON}/cdn/{version}/img/champion/{cid}.png", timeout=timeout)
            r.raise_for_status()
            (cache / f"{cid}.png").write_bytes(r.content)
            out[cid] = cache / f"{cid}.png"
        except Exception as e:
            log.warning("Portrait de %s introuvable (%s)", cid, e)
    log.info("Portraits prêts : %s", ", ".join(sorted(out)))
    return out
