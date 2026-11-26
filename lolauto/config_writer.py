"""Writes config.toml (used by the app): a complete, commented file that `config.load` reads back."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .config import Config

COMMENTS = {
    "network.director_host": "local IP of the streaming PC (OBS + director): agents send their score there",
    "network.director_port": "UDP port (allow it in the streaming PC's firewall)",
    "network.token": "same value on every PC: the director ignores other packets",
    "riot.api_url": "local LoL client API (only changed by the simulator)",
    "riot.poll_interval": "seconds between two reads",
    "obs.password": "OBS > Tools > WebSocket Server Settings",
    "obs.scene_split": "split-screen scene; \"\" to disable",
    "players.id": "A to E; the streaming PC is usually A",
    "players.scene": "OBS scene of this player's POV",
    "players.vision_source": "OBS source read by the vision (game capture or NDI); \"\" = no vision",
    "players.width": "this player's game resolution",
    "players.minimap": "minimap area for this player ([] = the one in [vision])",
    "switching.min_hold": "minimum time on a POV before a normal switch (s)",
    "switching.margin": "score lead needed for a normal switch",
    "switching.fast_min_hold": "urgent switch allowed after N s…",
    "switching.fast_margin": "…if the other player leads by at least this much",
    "switching.stale_after": "agent silent for N s = ignored",
    "switching.manual_pause": "manual scene change → automation pauses for N s",
    "switching.split_mode": "\"separate_fights\" (SPLIT only for separate fights) or \"always\"",
    "switching.split_threshold": "BOTH scores above this threshold…",
    "switching.split_enter_after": "…for N s → SPLIT",
    "switching.split_exit_below": "leave SPLIT when the lower score drops below this…",
    "switching.split_min_hold": "…and SPLIT has been shown for N s",
    "vision.interval": "seconds between two minimap reads",
    "vision.minimap": "minimap area [x0, y0, x1, y1] as fractions of the image (calibrated for 16:9)",
    "vision.near_radius": "\"nearby enemy\" = closer than this fraction of the minimap to the camera",
    "vision.together_dist": "two players' cameras closer than this = same fight (no SPLIT)",
    "vision.w_enemy_near": "bonus per nearby enemy ABOVE the player's usual count",
    "vision.enemy_champions": "force the enemy champions (replays); [] = read from the game API",
}

HEADER = """# =====================================================================
#  LoL Auto Director — configuration (the SAME file on every PC)
#  Written by the app. You can edit it by hand: it is read back as is.
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
    raise TypeError(f"value cannot be serialized: {v!r}")


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

    out.append("\n# One block per player / gaming PC (2 to 5).")
    for p in cfg.players:
        out.append("[[players]]")
        for f in dataclasses.fields(p):
            out.append(_line("players", f.name, getattr(p, f.name)))
        out.append("")

    table("switching", cfg.switching)
    table("scoring", cfg.scoring, skip=("role_bias", "events", "objectives"))
    out.append("\n# Small base bonus by role.")
    out.append("[scoring.role_bias]")
    out += [_line("scoring.role_bias", k, v) for k, v in cfg.scoring.role_bias.items()]
    out.append("\n# Points when the player takes part in the event (killer, assist…).")
    out.append("[scoring.events]")
    out += [_line("scoring.events", k, v) for k, v in cfg.scoring.events.items()]
    out.append("\n# Objective windows (`at` = game time in s). Examples: check them for the current season.")
    for o in cfg.scoring.objectives:
        out.append("[[scoring.objectives]]")
        out += [_line("scoring.objectives", k, v) for k, v in o.items()]
        out.append("")
    table("vision", cfg.vision)
    return "\n".join(out).rstrip() + "\n"


def save(cfg: Config, path: str | Path) -> None:
    from . import config as cfgmod

    text = dumps(cfg)
    cfgmod.loads(text)                    # never write a file that could not be read back
    Path(path).write_text(text, encoding="utf-8")
