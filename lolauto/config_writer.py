"""Écriture de config.toml (utilisée par le lanceur) : fichier complet, commenté, relisible par `config.load`."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .config import Config

COMMENTS = {
    "network.director_host": "IP locale du PC qui diffuse (OBS + directeur) : les agents y envoient leur score",
    "network.director_port": "port UDP (à ouvrir dans le pare-feu du PC qui diffuse)",
    "network.token": "même valeur sur tous les PC : le directeur ignore les autres paquets",
    "riot.api_url": "API locale du client LoL (ne change que pour le simulateur)",
    "riot.poll_interval": "secondes entre deux lectures",
    "obs.password": "OBS > Outils > Paramètres du serveur WebSocket",
    "obs.scene_split": "écran partagé ; \"\" pour désactiver",
    "players.id": "A à E ; le PC qui diffuse est en général A",
    "players.scene": "scène OBS de son POV",
    "players.vision_source": "source OBS lue par la vision (capture de jeu ou NDI) ; \"\" = pas de vision",
    "players.width": "résolution du jeu de ce joueur",
    "players.minimap": "zone de minimap propre à ce joueur ([] = celle de [vision])",
    "switching.min_hold": "durée minimale sur un POV avant un changement normal (s)",
    "switching.margin": "avance de score requise pour un changement normal",
    "switching.fast_min_hold": "changement urgent possible après N s…",
    "switching.fast_margin": "…si l'autre a au moins cette avance",
    "switching.stale_after": "agent silencieux depuis N s = ignoré",
    "switching.manual_pause": "changement de scène à la main → l'auto se tait N s",
    "switching.split_mode": "\"combats_separes\" (SPLIT seulement si combats différents) ou \"toujours\"",
    "switching.split_threshold": "les DEUX scores au-dessus de ce seuil…",
    "switching.split_enter_after": "…pendant N s → SPLIT",
    "switching.split_exit_below": "on sort du SPLIT quand le plus bas repasse sous ce seuil…",
    "switching.split_min_hold": "…et que le SPLIT est affiché depuis N s",
    "vision.interval": "secondes entre deux lectures de minimap",
    "vision.minimap": "zone de la minimap [x0, y0, x1, y1] en fractions de l'image (calibrée en 16:9)",
    "vision.near_radius": "« ennemi proche » = à moins de cette fraction de minimap de la caméra",
    "vision.together_dist": "caméras de deux joueurs plus proches = même combat (pas de SPLIT)",
    "vision.w_enemy_near": "bonus par ennemi proche EN PLUS de l'habituel du joueur",
    "vision.enemy_champions": "forcer les champions adverses (rediff) ; [] = lus dans l'API du jeu",
}

HEADER = """# =====================================================================
#  lol-auto-director — configuration (le MÊME fichier sur tous les PC)
#  Fichier généré par le lanceur : tu peux le modifier à la main, il sera relu tel quel.
# =====================================================================
"""


def _val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_val(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"valeur non sérialisable : {v!r}")


def _key(k: str) -> str:
    return k if k.replace("_", "").isalnum() else json.dumps(k)


def _line(section: str, name: str, value) -> str:
    text = f"{name} = {_val(value)}"
    c = COMMENTS.get(f"{section}.{name}")
    return f"{text:<40} # {c}" if c else text


def dumps(cfg: Config) -> str:
    out = [HEADER]

    def table(name: str, obj, skip=()):
        out.append(f"\n[{name}]")
        for f in dataclasses.fields(obj):
            if f.name in skip:
                continue
            out.append(_line(name, f.name, getattr(obj, f.name)))

    table("network", cfg.network)
    table("riot", cfg.riot)
    table("obs", cfg.obs)

    out.append("\n# Un bloc par joueur / PC de jeu (2 à 5).")
    for p in cfg.players:
        out.append("[[players]]")
        for f in dataclasses.fields(p):
            out.append(_line("players", f.name, getattr(p, f.name)))
        out.append("")

    table("switching", cfg.switching)
    table("scoring", cfg.scoring, skip=("role_bias", "events", "objectives"))
    out.append("\n# Petit bonus de base selon le rôle.")
    out.append("[scoring.role_bias]")
    out += [_line("scoring.role_bias", k, v) for k, v in cfg.scoring.role_bias.items()]
    out.append("\n# Points quand le joueur est acteur de l'événement (tueur, assist…).")
    out.append("[scoring.events]")
    out += [_line("scoring.events", k, v) for k, v in cfg.scoring.events.items()]
    out.append("\n# Fenêtres d'objectifs (`at` = temps de jeu en s). Exemples : à vérifier selon la saison.")
    for o in cfg.scoring.objectives:
        out.append("[[scoring.objectives]]")
        out += [_line("scoring.objectives", k, v) for k, v in o.items()]
        out.append("")
    table("vision", cfg.vision)
    return "\n".join(out).rstrip() + "\n"


def save(cfg: Config, path: str | Path) -> None:
    from . import config as cfgmod

    text = dumps(cfg)
    cfgmod.loads(text)                    # ne jamais écrire un fichier que l'on ne saurait pas relire
    Path(path).write_text(text, encoding="utf-8")
