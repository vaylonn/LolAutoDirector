#!/usr/bin/env python3
"""Faux serveur « Live Client Data » pour tester TOUT le pipeline sans lancer le jeu.

    python -m lolauto mock --who A --port 12999
    python -m lolauto mock --who B --port 13000

Il rejoue un scénario de ~60 s (combats, kills, mort, combat commun, puis deux combats séparés) au format de l'API réelle.
Avec `--loop 75`, le scénario recommence toutes les 75 s (faux coéquipier « vivant » pendant une vraie partie) ;
le temps de jeu annoncé reste alors entre 60 et 60+N s, hors des fenêtres d'objectifs de config.toml.
Utilise ensuite `--api-url http://127.0.0.1:12999` côté agent (voir `python -m lolauto simulate`).
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PLAYERS = {"A": ("Alice", "MIDDLE"), "B": ("Bob", "JUNGLE")}
MAX_HP = 2000.0

# (temps, ratio de vie) — interpolation linéaire
HP = {
    "A": [(0, 1), (17, 1), (21, 0.2), (21.01, 0), (33, 0), (33.01, 1), (34, 1), (37, 0.35), (38, 0.3), (42, 1), (50, 1), (52, 0.55), (54, 0.3), (56, 0.15), (58, 0.15), (62, 1), (70, 1)],
    "B": [(0, 1), (6, 1), (9, 0.35), (14, 1), (34, 1), (37, 0.4), (40, 1), (50, 1), (52, 0.6), (54, 0.35), (56, 0.2), (58, 0.2), (62, 1), (70, 1)],
}
DEAD = {"A": [(21.0, 33.0)], "B": []}

EVENTS = [
    (0.0, {"EventName": "GameStart"}),
    (9.0, {"EventName": "ChampionKill", "KillerName": "Bob", "VictimName": "Enemy1", "Assisters": []}),
    (21.0, {"EventName": "ChampionKill", "KillerName": "Enemy2", "VictimName": "Alice", "Assisters": []}),
    (38.0, {"EventName": "ChampionKill", "KillerName": "Alice", "VictimName": "Enemy3", "Assisters": ["Bob"]}),
    # deux combats SÉPARÉS : chacun son kill, personne en commun
    (53.0, {"EventName": "ChampionKill", "KillerName": "Alice", "VictimName": "Enemy4", "Assisters": []}),
    (53.5, {"EventName": "ChampionKill", "KillerName": "Bob", "VictimName": "Enemy5", "Assisters": []}),
]


def interp(keys, t):
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0) if t1 > t0 else v1
    return keys[-1][1]


def snapshot(who: str, t: float, game_time: float | None = None) -> dict:
    name, role = PLAYERS[who]
    dead = any(a <= t < b for a, b in DEAD[who])
    ratio = interp(HP[who], t)
    events = [dict(ev, EventID=i, EventTime=et) for i, (et, ev) in enumerate(EVENTS) if et <= t]
    players = [
        {"riotIdGameName": n, "summonerName": f"{n}#EUW", "position": r,
         "isDead": (p == who and dead), "team": "ORDER", "level": 8}
        for p, (n, r) in PLAYERS.items()
        for n, r in [(n, r)]
    ] + [{"riotIdGameName": f"Enemy{i}", "summonerName": f"Enemy{i}#EUW", "position": "TOP",
          "isDead": False, "team": "CHAOS", "level": 8} for i in range(1, 4)]
    return {
        "activePlayer": {"summonerName": f"{name}#EUW", "level": 8,
                         "championStats": {"currentHealth": ratio * MAX_HP, "maxHealth": MAX_HP}},
        "allPlayers": players,
        "events": {"Events": events},
        "gameData": {"gameMode": "CLASSIC", "gameTime": t if game_time is None else game_time},
    }


def scenario_time(elapsed: float, speed: float, loop: float) -> tuple[float, float]:
    """(temps dans le scénario, temps de jeu annoncé)."""
    t = elapsed * speed
    if loop > 0:
        ts = t % loop
        return ts, 60.0 + ts
    return t, t


def make_handler(who: str, speed: float, start: float, loop: float = 0.0):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_GET(self):
            t, gt = scenario_time(time.monotonic() - start, speed, loop)
            if self.path.endswith("/allgamedata"):
                body = snapshot(who, t, gt)
            elif self.path.endswith("/activeplayername"):
                body = f"{PLAYERS[who][0]}#EUW"
            else:
                self.send_error(404)
                return
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return H


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--who", choices=["A", "B"], required=True)
    ap.add_argument("--port", type=int, default=2999)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--loop", type=float, default=0.0, help="rejoue le scénario toutes les N secondes (0 = une fois)")
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.who, a.speed, time.monotonic(), a.loop))
    loop = f", boucle toutes les {a.loop:.0f} s" if a.loop > 0 else ""
    print(f"mock API {a.who} sur http://127.0.0.1:{a.port}{loop}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
