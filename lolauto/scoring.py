"""Calcul du « score d'intérêt » d'un joueur à partir de l'API locale de League of Legends.

Ce module est volontairement pur (aucun réseau, aucune horloge interne) : l'heure `now`
est passée par l'appelant, ce qui le rend trivial à tester.

Ce que l'API locale (Live Client Data) donne VRAIMENT : sa propre vie, son état mort/vivant,
son rôle, la liste des événements de la partie (kills, objectifs…) et le temps de jeu.
Elle ne donne PAS de coordonnées : le score se base donc sur l'intensité d'un combat
(vie qui chute), les événements, le rôle et des fenêtres d'objectifs configurables.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

DEFAULT_EVENTS = {
    "ChampionKill": 6.0,
    "Multikill": 4.0,
    "FirstBlood": 3.0,
    "DragonKill": 3.0,
    "BaronKill": 4.0,
    "HeraldKill": 3.0,
    "TurretKilled": 2.0,
    "InhibKilled": 3.0,
}


@dataclass
class ScoringConfig:
    window: float = 3.0            # fenêtre (s) sur laquelle on mesure la vie perdue
    w_hp_drop: float = 4.0         # poids de la vie perdue récemment (0..1 de vie max)
    w_low_hp: float = 1.5          # poids de « je suis bas en vie »
    dead_score: float = -1.0       # score d'un joueur mort (pour céder la caméra à l'autre)
    death_bonus: float = 4.0       # on laisse voir la mort quelques secondes…
    death_duration: float = 3.0    # …pendant cette durée
    event_duration: float = 6.0    # durée de décroissance du bonus d'un événement
    objective_lead: float = 30.0   # fenêtre d'objectif : secondes AVANT l'apparition
    objective_after: float = 45.0  # …et secondes APRÈS
    shared_window: float = 10.0    # durée pendant laquelle un événement compte pour « même combat »
    role_bias: dict = field(default_factory=lambda: {"JUNGLE": 0.4, "UTILITY": 0.1})
    events: dict = field(default_factory=lambda: dict(DEFAULT_EVENTS))
    objectives: list = field(default_factory=list)


@dataclass
class Result:
    score: float
    dead: bool           # mort en ce moment
    settled_dead: bool   # mort ET la « minute de gloire » de la mort est passée
    hp_ratio: float
    role: str
    game_time: float
    parts: dict
    recent_events: list = field(default_factory=list)  # EventID récents où je suis impliqué


def short(name: str | None) -> str:
    """'Pseudo#EUW' -> 'pseudo' (les événements n'incluent pas toujours le tag)."""
    return (name or "").split("#", 1)[0].strip().casefold()


def same(a: str | None, b: str | None) -> bool:
    sa, sb = short(a), short(b)
    return bool(sa) and sa == sb


class Scorer:
    def __init__(self, cfg: ScoringConfig | None = None):
        self.cfg = cfg or ScoringConfig()
        self.reset()

    def reset(self) -> None:
        self._hist: deque = deque()
        self._seen: int | None = None   # nb d'événements déjà traités (None = 1er poll)
        self._bonuses: list = []        # (kind, valeur, t0, durée)
        self._death_t: float | None = None
        self._last_game_time = 0.0
        self._involved: list = []       # (EventID, t) où je suis tueur/assist/victime

    # ------------------------------------------------------------------ événements
    def _on_event(self, ev: dict, me: str, now: float) -> None:
        name = ev.get("EventName")
        people = [ev.get("KillerName"), ev.get("VictimName"), ev.get("Recipient"), *(ev.get("Assisters") or [])]
        if ev.get("EventID") is not None and name != "Multikill" and any(same(p, me) for p in people):
            self._involved.append((ev["EventID"], now))
        if name == "ChampionKill" and same(ev.get("VictimName"), me):
            self._bonuses.append(("death", self.cfg.death_bonus, now, self.cfg.death_duration))
            self._death_t = now
        value = self.cfg.events.get(name)
        if value is None:
            return
        actors = [ev.get("KillerName"), ev.get("Recipient"), *(ev.get("Assisters") or [])]
        if any(same(a, me) for a in actors):
            self._bonuses.append(("event", float(value), now, self.cfg.event_duration))

    # ------------------------------------------------------------------ calcul
    def update(self, data: dict, me: str, now: float) -> Result | None:
        cfg = self.cfg
        stats = (data.get("activePlayer") or {}).get("championStats")
        if not stats:
            return None  # spectateur / replay / chargement : pas de joueur actif

        game_time = float((data.get("gameData") or {}).get("gameTime", 0.0))
        events = (data.get("events") or {}).get("Events", []) or []

        # nouvelle partie détectée (liste d'événements plus courte ou temps qui recule)
        if (self._seen is not None and len(events) < self._seen) or game_time + 5 < self._last_game_time:
            self.reset()
        self._last_game_time = game_time

        if self._seen is None:
            self._seen = len(events)  # on ignore l'historique si on démarre en cours de partie
        else:
            for ev in events[self._seen:]:
                self._on_event(ev, me, now)
            self._seen = len(events)

        # vie
        cur, mx = float(stats.get("currentHealth", 0.0)), float(stats.get("maxHealth", 0.0))
        ratio = max(0.0, min(1.0, cur / mx)) if mx > 0 else 1.0
        self._hist.append((now, ratio))
        while self._hist and now - self._hist[0][0] > cfg.window:
            self._hist.popleft()
        drop = max(0.0, max(r for _, r in self._hist) - ratio)

        # état mort/vivant + rôle
        dead, role = False, ""
        for p in data.get("allPlayers", []) or []:
            names = (p.get("riotIdGameName"), p.get("summonerName"), p.get("riotId"))
            if any(same(n, me) for n in names if n):
                dead = bool(p.get("isDead"))
                role = (p.get("position") or "").upper()
                break
        if not dead:
            self._death_t = None

        # bonus qui décroissent linéairement
        event_part = death_part = 0.0
        alive_bonuses = []
        for kind, val, t0, dur in self._bonuses:
            f = 1.0 - (now - t0) / dur if dur > 0 else 0.0
            if f <= 0:
                continue
            alive_bonuses.append((kind, val, t0, dur))
            if kind == "death":
                death_part += val * f
            else:
                event_part += val * f
        self._bonuses = alive_bonuses

        settled = dead and (self._death_t is None or now - self._death_t >= cfg.death_duration)

        parts = {"drop": 0.0, "low_hp": 0.0, "role": 0.0, "events": 0.0, "objective": 0.0, "death": 0.0}
        if dead:
            parts["death"] = death_part
            score = cfg.dead_score + death_part
        else:
            parts["drop"] = drop * cfg.w_hp_drop
            parts["low_hp"] = (1.0 - ratio) * cfg.w_low_hp
            parts["role"] = float(cfg.role_bias.get(role, 0.0))
            parts["events"] = event_part
            parts["objective"] = self._objective_bonus(game_time, role)
            score = sum(parts.values())

        self._involved = [(i, t) for i, t in self._involved if now - t <= cfg.shared_window]
        return Result(round(score, 3), dead, settled, ratio, role, game_time,
                      {k: round(v, 3) for k, v in parts.items()},
                      sorted({i for i, _ in self._involved}))

    def _objective_bonus(self, game_time: float, role: str) -> float:
        total = 0.0
        for o in self.cfg.objectives:
            at = float(o["at"])
            lead = float(o.get("lead", self.cfg.objective_lead))
            after = float(o.get("after", self.cfg.objective_after))
            roles = [r.upper() for r in o.get("roles", [])]
            if at - lead <= game_time <= at + after and (not roles or role in roles):
                total += float(o.get("bonus", 1.5))
        return total
