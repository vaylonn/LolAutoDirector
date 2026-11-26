"""POV switching decisions for 2 to 5 players (pure logic, no OBS and no network)."""

from __future__ import annotations

from dataclasses import dataclass

NEG_INF = float("-inf")
SPLIT = "SPLIT"
# values written by earlier versions
LEGACY_SPLIT_MODES = {"combats_separes": "separate_fights", "toujours": "always"}


@dataclass
class SwitchConfig:
    min_hold: float = 5.0          # minimum time on a POV before a "normal" switch
    margin: float = 1.0            # score lead needed for a normal switch
    fast_min_hold: float = 2.0     # minimum time before an "urgent" switch
    fast_margin: float = 5.0       # score lead needed for an urgent switch
    stale_after: float = 3.0       # an agent silent for more than N s is ignored
    split_enabled: bool = True     # split screen when two players are in action
    split_threshold: float = 3.0   # BOTH scores must exceed this threshold…
    split_enter_after: float = 1.5 # …for at least N s
    split_exit_below: float = 1.5  # leave SPLIT when the lower score drops below this…
    split_min_hold: float = 6.0    # …and SPLIT has been shown for at least N s
    manual_pause: float = 20.0     # automation pause after a manual switch in OBS
    # "separate_fights": no SPLIT when both players are in the SAME fight (shared recent event, or close
    # cameras on the minimap when the vision is on); "always": SPLIT as soon as both are in action
    split_mode: str = "separate_fights"

    def __post_init__(self):
        self.split_mode = LEGACY_SPLIT_MODES.get(self.split_mode, self.split_mode)
        if self.split_mode not in ("always", "separate_fights"):
            raise ValueError(f'split_mode must be "always" or "separate_fights", not {self.split_mode!r}')


def _pair(p: str, q: str) -> tuple[str, str]:
    return (p, q) if p <= q else (q, p)


class Switcher:
    """Takes the agents' scores (A…E) and says when and where to switch.

    `decide()` returns a player id, "SPLIT" (the pair to show is then in `pending_pair`) or None.
    It does not change the current state: the caller confirms with `apply()` only once OBS has
    accepted the switch.
    """

    def __init__(self, cfg: SwitchConfig, start: str = "A"):
        self.cfg = cfg
        self.current = start
        self.split_pair: tuple[str, str] | None = None     # pair shown when current == SPLIT
        self.pending_pair: tuple[str, str] | None = None   # pair proposed by decide()
        self.last_switch = NEG_INF
        self._score: dict[str, float | None] = {}
        self._dead: dict[str, bool] = {}
        self._seen: dict[str, float] = {}
        self._high_since: dict[tuple, float] = {}
        self._events: dict[str, set] = {}
        self._together_until: dict[tuple, float] = {}       # pairs seen "together" (vision)

    # ------------------------------------------------------------------ inputs
    def feed(self, pid: str, score: float | None, settled_dead: bool, now: float, events=()) -> None:
        self._seen[pid] = now
        self._score[pid] = score
        self._dead[pid] = bool(settled_dead)
        self._events[pid] = set(events or ())

    def mark_together(self, now: float, pair: tuple[str, str] | None = None, hold: float = 3.0) -> None:
        """External signal (vision): these two players are in the same place (None = everyone)."""
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

    # ------------------------------------------------------------------ decision
    def _best(self, live: dict, exclude=()) -> str | None:
        """The most interesting player (alive first); ties go to alphabetical order."""
        cands = [p for p in live if p not in exclude]
        if not cands:
            return None
        return min(cands, key=lambda p: (bool(self._dead.get(p)), -live[p], p))

    def _split_candidate(self, live: dict, now: float, held: float) -> tuple[str, str] | None:
        """The two best players, if both have been in action (and not together) long enough."""
        cfg = self.cfg
        if not cfg.split_enabled or len(live) < 2:
            self._high_since.clear()
            return None
        ranked = sorted(live, key=lambda p: (-live[p], p))
        p, q = ranked[0], ranked[1]
        key = _pair(p, q)
        together = cfg.split_mode == "separate_fights" and self.same_fight(now, p, q)
        if min(live[p], live[q]) < cfg.split_threshold or together:
            self._high_since.pop(key, None)
            return None
        since = self._high_since.setdefault(key, now)
        for other in [k for k in self._high_since if k != key]:
            del self._high_since[other]
        # two fights at once are "urgent": fast threshold, not min_hold
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

        # only one POV available (the others are not in game or crashed): go there
        if len(live) == 1:
            only = next(iter(live))
            self._high_since.clear()
            return None if self.current == only else only

        if self.current == SPLIT:
            if self.split_pair is None:               # SPLIT picked by hand in OBS: the two best
                ranked = sorted(live, key=lambda k: (-live[k], k))
                self.split_pair = _pair(ranked[0], ranked[1])
            p, q = self.split_pair
            if p not in live or q not in live:
                return self._best(live)
            higher = p if (live[p], q) >= (live[q], p) else q
            if cfg.split_mode == "separate_fights" and self.same_fight(now, p, q) and held >= cfg.fast_min_hold:
                return higher                         # same fight: one POV is enough
            if min(live[p], live[q]) < cfg.split_exit_below and held >= cfg.split_min_hold:
                return higher
            if held >= cfg.split_min_hold:            # another pair became more interesting
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
        if cur not in live:                           # the shown player stopped sending
            return self._best(live)
        if self._high_since and not self._dead.get(cur):
            return None                               # a SPLIT is coming: no back-and-forth just before
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
