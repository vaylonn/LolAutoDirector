"""Décision de changement de POV pour 2 à 5 joueurs (logique pure, sans OBS ni réseau)."""

from __future__ import annotations

from dataclasses import dataclass

NEG_INF = float("-inf")
SPLIT = "SPLIT"


@dataclass
class SwitchConfig:
    min_hold: float = 5.0          # durée minimale sur un POV avant un changement « normal »
    margin: float = 1.0            # avantage de score requis pour un changement normal
    fast_min_hold: float = 2.0     # durée minimale pour un changement « urgent »
    fast_margin: float = 5.0       # avantage requis pour un changement urgent
    stale_after: float = 3.0       # un agent silencieux plus de N s est ignoré
    split_enabled: bool = True     # écran partagé quand deux joueurs sont dans l'action
    split_threshold: float = 3.0   # les DEUX scores doivent dépasser ce seuil…
    split_enter_after: float = 1.5 # …pendant au moins N s
    split_exit_below: float = 1.5  # on quitte le SPLIT quand le plus bas passe sous ce seuil…
    split_min_hold: float = 6.0    # …et que le SPLIT est affiché depuis au moins N s
    manual_pause: float = 20.0     # pause de l'auto après un changement manuel dans OBS
    # "combats_separes" : pas de SPLIT si les deux joueurs sont dans le MÊME combat (événement commun
    # récent, ou minimaps proches si la vision est active) ; "toujours" : SPLIT dès que les deux sont actifs
    split_mode: str = "combats_separes"

    def __post_init__(self):
        if self.split_mode not in ("toujours", "combats_separes"):
            raise ValueError(f'split_mode doit être "toujours" ou "combats_separes", pas {self.split_mode!r}')


def _pair(p: str, q: str) -> tuple[str, str]:
    return (p, q) if p <= q else (q, p)


class Switcher:
    """Reçoit les scores des agents (A…E), dit quand et vers où basculer.

    `decide()` renvoie un identifiant de joueur, "SPLIT" (la paire à montrer est alors dans
    `pending_pair`) ou None. Il ne modifie pas l'état courant : l'appelant confirme avec `apply()`
    seulement si OBS a bien accepté le changement.
    """

    def __init__(self, cfg: SwitchConfig, start: str = "A"):
        self.cfg = cfg
        self.current = start
        self.split_pair: tuple[str, str] | None = None     # paire affichée quand current == SPLIT
        self.pending_pair: tuple[str, str] | None = None   # paire proposée par decide()
        self.last_switch = NEG_INF
        self._score: dict[str, float | None] = {}
        self._dead: dict[str, bool] = {}
        self._seen: dict[str, float] = {}
        self._high_since: dict[tuple, float] = {}
        self._events: dict[str, set] = {}
        self._together_until: dict[tuple, float] = {}       # paires « ensemble » (vision)

    # ------------------------------------------------------------------ entrées
    def feed(self, pid: str, score: float | None, settled_dead: bool, now: float, events=()) -> None:
        self._seen[pid] = now
        self._score[pid] = score
        self._dead[pid] = bool(settled_dead)
        self._events[pid] = set(events or ())

    def mark_together(self, now: float, pair: tuple[str, str] | None = None, hold: float = 3.0) -> None:
        """Signal externe (vision) : ces deux joueurs sont au même endroit (None = tous)."""
        self._together_until[_pair(*pair) if pair else None] = now + hold

    def same_fight(self, now: float, p: str = "A", q: str = "B") -> bool:
        if now < self._together_until.get(None, NEG_INF) or now < self._together_until.get(_pair(p, q), NEG_INF):
            return True
        return bool(self._events.get(p, set()) & self._events.get(q, set()))

    def live(self, now: float) -> dict[str, float]:
        return {p: s for p, s in self._score.items()
                if s is not None and now - self._seen.get(p, NEG_INF) <= self.cfg.stale_after}

    def apply(self, target: str, now: float, pair: tuple[str, str] | None = None) -> None:
        self.current = target
        self.split_pair = (pair or self.pending_pair) if target == SPLIT else None
        self.last_switch = now
        self._high_since.clear()

    # ------------------------------------------------------------------ décision
    def _best(self, live: dict, exclude=()) -> str | None:
        """Le joueur le plus intéressant (vivant de préférence), à score égal l'ordre alphabétique."""
        cands = [p for p in live if p not in exclude]
        if not cands:
            return None
        return min(cands, key=lambda p: (bool(self._dead.get(p)), -live[p], p))

    def _split_candidate(self, live: dict, now: float, held: float) -> tuple[str, str] | None:
        """Les deux meilleurs, s'ils sont tous les deux dans l'action (et pas ensemble) depuis assez longtemps."""
        cfg = self.cfg
        if not cfg.split_enabled or len(live) < 2:
            self._high_since.clear()
            return None
        ranked = sorted(live, key=lambda p: (-live[p], p))
        p, q = ranked[0], ranked[1]
        key = _pair(p, q)
        together = cfg.split_mode == "combats_separes" and self.same_fight(now, p, q)
        if min(live[p], live[q]) < cfg.split_threshold or together:
            self._high_since.pop(key, None)
            return None
        since = self._high_since.setdefault(key, now)
        for other in [k for k in self._high_since if k != key]:
            del self._high_since[other]
        # un combat à deux est « urgent » : seuil rapide, pas min_hold
        if now - since >= cfg.split_enter_after and held >= cfg.fast_min_hold:
            return key
        return None

    def decide(self, now: float) -> str | None:
        cfg = self.cfg
        live = self.live(now)
        self.pending_pair = None
        if not live:
            return None
        held = now - self.last_switch

        # un seul POV disponible (les autres ne sont pas en partie / ont planté) : on y va
        if len(live) == 1:
            only = next(iter(live))
            self._high_since.clear()
            return None if self.current == only else only

        if self.current == SPLIT:
            if self.split_pair is None:               # SPLIT choisi à la main dans OBS : les deux meilleurs
                ranked = sorted(live, key=lambda k: (-live[k], k))
                self.split_pair = _pair(ranked[0], ranked[1])
            p, q = self.split_pair
            if p not in live or q not in live:
                return self._best(live)
            higher = p if (live[p], q) >= (live[q], p) else q
            if cfg.split_mode == "combats_separes" and self.same_fight(now, p, q) and held >= cfg.fast_min_hold:
                return higher                         # même combat : un seul POV suffit
            if min(live[p], live[q]) < cfg.split_exit_below and held >= cfg.split_min_hold:
                return higher
            if held >= cfg.split_min_hold:            # une autre paire est devenue plus intéressante
                cand = self._split_candidate(live, now, held)
                if cand and cand != _pair(p, q):
                    self.pending_pair = cand
                    return SPLIT
            return None

        cand = self._split_candidate(live, now, held)
        if cand:
            self.pending_pair = cand
            return SPLIT

        cur = self.current
        if cur not in live:                           # le joueur affiché n'envoie plus rien
            return self._best(live)
        if self._high_since and not self._dead.get(cur):
            return None                               # un SPLIT se prépare : pas de va-et-vient juste avant
        oth = self._best(live, exclude=(cur,))
        if oth is None:
            return None
        diff = live[oth] - live[cur]
        if self._dead.get(cur) and not self._dead.get(oth) and held >= cfg.fast_min_hold:
            return oth
        if diff >= cfg.fast_margin and held >= cfg.fast_min_hold:
            return oth
        if diff >= cfg.margin and held >= cfg.min_hold:
            return oth
        return None
