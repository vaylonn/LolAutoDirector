"""Agent : tourne sur CHAQUE PC de jeu. Lit l'API locale LoL, calcule un score, l'envoie au directeur (UDP).

    python -m lolauto.agent --id A     # sur ton PC
    python -m lolauto.agent --id B     # sur le MacBook de ton pote
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import time

import requests

from . import config as cfgmod
from . import logs
from .riot_api import RiotClient
from .portraits import enemy_ids
from .scoring import Scorer, same

log = logging.getLogger("agent")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Agent LoL → directeur")
    ap.add_argument("--id", required=True, choices=["A", "B", "C", "D", "E"], help="identité de ce PC (joueur)")
    ap.add_argument("--config", help="chemin de config.toml")
    ap.add_argument("--api-url", help="remplace [riot].api_url (simulateur)")
    ap.add_argument("--director-host", help="remplace [network].director_host")
    ap.add_argument("--director-port", type=int, help="remplace [network].director_port")
    ap.add_argument("--once", action="store_true", help="un seul relevé affiché puis quitte (diagnostic)")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--log-file", help="écrit aussi le journal dans ce fichier (ex. logs/agent_A.log)")
    return ap


class Agent:
    """Un relevé = un message pour le directeur. Séparé de la boucle réseau pour être testable."""

    def __init__(self, pid: str, cfg, client):
        self.pid, self.cfg, self.client = pid, cfg, client
        self.scorer = Scorer(cfg.scoring)
        self.me: str | None = None
        self.enemies: list[str] = []
        self.in_game = False
        self.last: object = None          # dernier Result (diagnostic)

    def step(self, now: float) -> dict:
        msg = {"v": 1, "token": self.cfg.network.token, "id": self.pid,
               "in_game": False, "score": None, "dead": False}
        try:
            data = self.client.all_game_data()
            self.me = self.me or self.client.active_name()
            res = self.scorer.update(data, self.me, now)
            if res is not None:
                if not self.enemies:
                    self.enemies = enemy_ids(data.get("allPlayers") or [], self.me, same)
                msg.update(in_game=True, score=round(res.score, 3), dead=res.settled_dead, enemies=self.enemies,
                           hp=round(res.hp_ratio, 3), role=res.role, gt=round(res.game_time, 1),
                           ev=res.recent_events)
                self.last = res
        except requests.RequestException:
            if self.in_game:
                log.info("Partie terminée / API injoignable : en attente d'une nouvelle partie")
            self.me, self.enemies, self.last = None, [], None
            self.scorer.reset()
        except Exception:  # un bug ne doit jamais tuer l'agent en plein live
            log.exception("Erreur inattendue (l'agent continue)")
        if msg["in_game"] and not self.in_game:
            log.info("Partie détectée (joueur : %s)", self.me)
        self.in_game = msg["in_game"]
        return msg


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logs.setup(args.verbose, args.log_file, "%(asctime)s %(name)s %(message)s")
    cfg = cfgmod.load(args.config)
    target = (args.director_host or cfg.network.director_host,
              args.director_port or cfg.network.director_port)
    client = RiotClient(args.api_url or cfg.riot.api_url, cfg.riot.timeout)
    agent = Agent(args.id, cfg, client)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    log.info("Agent %s → directeur %s:%s | API %s", args.id, target[0], target[1], client.base)
    last_log = 0.0

    while True:
        t0 = time.monotonic()
        msg = agent.step(t0)
        if args.once:
            if agent.last is None:
                print("API injoignable : lance d'abord une partie (même personnalisée contre des bots).")
                return 1
            r = agent.last
            print(json.dumps({"me": agent.me, "score": r.score, "parts": r.parts, "role": r.role,
                              "hp": r.hp_ratio, "dead": r.dead, "ennemis": agent.enemies}, indent=2))
            return 0
        if msg["in_game"] and t0 - last_log > 1.0:
            last_log = t0
            log.debug("score=%.2f %s", msg["score"], agent.last.parts)
        try:
            sock.sendto(json.dumps(msg).encode(), target)
        except OSError as e:
            log.warning("Envoi UDP impossible : %s", e)
        time.sleep(max(0.0, cfg.riot.poll_interval - (time.monotonic() - t0)))


if __name__ == "__main__":
    raise SystemExit(main())
