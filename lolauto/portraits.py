"""Official champion portraits (Riot's Data Dragon), downloaded once then cached.

    https://ddragon.leagueoflegends.com/cdn/<version>/img/champion/<Id>.png   (120×120)

Ids come from the game's local API: `rawChampionName` = "game_character_displayname_<Id>".
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
    """Keeps only plausible champion ids (letters and digits).

    Names arrive from the network (agents' UDP packets) and are used to build a URL and a file
    name, so anything else ("../", spaces, etc.) is rejected.
    """
    if not isinstance(ids, (list, tuple)):
        return []
    return sorted({i for i in ids if isinstance(i, str) and _VALID_ID.match(i)})


def champion_id(player: dict) -> str:
    """Data Dragon id of a player from the API ("MonkeyKing" for Wukong, etc.)."""
    raw = player.get("rawChampionName") or ""
    if raw.startswith("game_character_displayname_"):
        return raw.split("game_character_displayname_", 1)[1]
    return (player.get("championName") or "").replace(" ", "").replace("'", "").replace(".", "")


def enemy_ids(all_players: list, me: str, same_name) -> list[str]:
    """Enemy team's champions, as seen from player `me`."""
    team = next((p.get("team") for p in all_players
                 if any(same_name(n, me) for n in (p.get("riotIdGameName"), p.get("summonerName")) if n)), None)
    if not team:
        return []
    return [cid for p in all_players if p.get("team") and p.get("team") != team and (cid := champion_id(p))]


def ensure_portraits(ids, cache_dir: str | Path = "data/portraits", timeout: float = 5.0) -> dict[str, Path]:
    """Downloads missing portraits. Returns {id: path} for the available ones."""
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
        log.warning("Portraits: Data Dragon unreachable (%s), vision runs without champion recognition", e)
        return out
    for cid in missing:
        try:
            r = requests.get(f"{DDRAGON}/cdn/{version}/img/champion/{cid}.png", timeout=timeout)
            r.raise_for_status()
            (cache / f"{cid}.png").write_bytes(r.content)
            out[cid] = cache / f"{cid}.png"
        except Exception as e:
            log.warning("Portrait for %s not found (%s)", cid, e)
    log.info("Portraits ready: %s", ", ".join(sorted(out)))
    return out
