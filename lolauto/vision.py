"""Minimap reading (OpenCV) from OBS images.

Why go through OBS: the director already talks to it, and OBS sees EVERY POV (the local game capture and
the teammates' NDI sources). Nothing has to be installed on the player PCs for the vision.

What is detected on each minimap:
- the white camera rectangle (= where the player is looking, in practice around their champion);
- enemy (red ring) and ally (blue ring) champion icons.

From that we get:
- a "danger / anticipation" bonus = enemies near the camera rectangle ABOVE this player's usual count
  (the lane opponent does not count), plus a bonus when one more enemy arrives;
- "together" = two players' camera rectangles are close → same fight (for split_mode).

Colour and size thresholds may need CALIBRATING for your setup (resolution, minimap size):
see `python -m lolauto vision-debug`.
"""

from __future__ import annotations

import base64
import logging
import math
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("vision")

try:  # OpenCV is optional: without it the vision is simply turned off
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None


@dataclass
class VisionConfig:
    enabled: bool = True
    interval: float = 0.25             # seconds between two reads (per player)
    # Minimap area as fractions of the image [x0, y0, x1, y1] (bottom-right corner by default)
    minimap: list = field(default_factory=lambda: [0.861, 0.753, 0.994, 0.988])
    # Colours (OpenCV HSV: H 0-180, S/V 0-255)
    red_h_low: int = 8                 # red = H <= red_h_low or H >= red_h_high
    red_h_high: int = 170
    blue_h: list = field(default_factory=lambda: [95, 125])
    ring_s_min: int = 110
    ring_v_min: int = 110
    white_s_max: int = 45
    white_v_min: int = 200
    # Champion icons: circles of radius icon_r_min..icon_r_max (fraction of the minimap width)
    icon_r_min: float = 0.032
    icon_r_max: float = 0.050
    ring_coverage: float = 0.8         # share of the circle that must be red/blue
    hough_sensitivity: int = 10        # lower = more candidate circles (HoughCircles param2)
    camera_min: float = 0.12
    camera_max: float = 0.55
    near_radius: float = 0.20          # "near" = within 20 % of the minimap from the camera centre
    together_dist: float = 0.15        # two cameras within 15 % → same fight
    w_enemy_near: float = 1.2          # bonus per nearby enemy…
    max_enemies: int = 5               # …capped
    stale_after: float = 1.5           # older reads are ignored
    # Portrait recognition (the game's champions, sent by the agent from the LoL API)
    enemy_champions: list = field(default_factory=list)  # force the list (replays), e.g. ["Ahri", "Zed"]
    portraits_dir: str = "data/portraits"
    portrait_strong: float = 0.80      # match good enough on its own…
    portrait_weak: float = 0.50        # …or average match + visible red ring
    portrait_ring: float = 0.45
    portrait_learn: float = 0.65       # above this (with a clear ring), the portrait is relearnt from the minimap
    # Stability / anticipation
    smooth_window: float = 0.75        # keep the max of nearby enemies over this window (blinking icons)
    w_enemy_approach: float = 0.8      # bonus when enemies are closing in (anticipation)
    approach_window: float = 1.5
    # Each player's "usual": median of nearby enemies over baseline_window seconds.
    # In lane, bot always faces 2 enemies and top faces 1: that is not action.
    # Only enemies ABOVE that usual count (gank, roam, teamfight).
    baseline_window: float = 60.0
    black_level: float = 4.0           # darker image = game not shown (Alt+Tab, loading) → ignored

    def __post_init__(self):
        if len(self.minimap) != 4 or not (0 <= self.minimap[0] < self.minimap[2] <= 1
                                         and 0 <= self.minimap[1] < self.minimap[3] <= 1):
            raise ValueError(f"[vision].minimap must be [x0, y0, x1, y1] between 0 and 1, not {self.minimap}")
        if self.interval <= 0:
            raise ValueError("[vision].interval must be > 0")


@dataclass
class Detection:
    camera: tuple | None               # centre of the camera rectangle (x, y) as minimap fractions
    enemies: list                      # [(x, y), …]
    allies: list
    t: float = 0.0
    names: list = field(default_factory=list)  # recognised champion for each enemy ("" = unknown)

    def nearest_enemy(self) -> float | None:
        if not self.camera or not self.enemies:
            return None
        cx, cy = self.camera
        return min(math.hypot(x - cx, y - cy) for x, y in self.enemies)

    def enemies_near(self, radius: float) -> int:
        if not self.camera:
            return 0
        cx, cy = self.camera
        return sum(math.hypot(x - cx, y - cy) <= radius for x, y in self.enemies)


# --------------------------------------------------------------------------- pure detection
def crop_minimap(frame, roi):
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = roi
    return frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


# Ring sampling points (radius 1): 16 sectors × 4 angles × 4 radii (0.82r..1.12r)
if np is not None:
    _ANG = np.radians(np.repeat(np.arange(16), 4) * 22.5 + np.tile([2.8, 8.4, 14.1, 19.7], 16))
    _F = np.repeat([0.82, 0.92, 1.02, 1.12], 64)
    _RCOS = np.tile(np.cos(_ANG), 4) * _F
    _RSIN = np.tile(np.sin(_ANG), 4) * _F
    _SECT4 = np.tile(np.repeat(np.arange(16), 4), 4)


def _ring_coverage(mask, x, y, r):
    """Share of the ring's 16 angular sectors (0.8r..1.15r) where the colour is present.

    Sectors outside the image (icon cut by the edge) are ignored.
    """
    h, w = mask.shape
    xs = np.rint(x + r * _RCOS).astype(int)
    ys = np.rint(y + r * _RSIN).astype(int)
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    n = np.bincount(_SECT4[ok], minlength=16)
    hits = np.bincount(_SECT4[ok], weights=mask[ys[ok], xs[ok]], minlength=16)
    seen = n >= 4
    if seen.sum() < 8:
        return 0.0
    return float(((hits[seen] / n[seen]) > 0.15).mean())


def _best_ring(mask, x, y, radii, step):
    """Hough gives a rough centre/radius: search around it for the best fit."""
    best = (0.0, x, y, radii[0])
    for dx in (-step, 0.0, step):
        for dy in (-step, 0.0, step):
            for r in radii:
                c = _ring_coverage(mask, x + dx, y + dy, r)
                if c > best[0]:
                    best = (c, x + dx, y + dy, r)
    return best


class PortraitBank:
    """Enemy champion portraits, searched as is on the minimap (normalised correlation).

    Portraits come from Data Dragon (official Riot images). Once a champion is recognised
    with certainty, its portrait is REPLACED by the icon seen on the minimap (the game's exact rendering),
    so recognition improves on its own during the game.
    """

    SCALES = (0.85, 1.0, 1.15)

    def __init__(self):
        self.images: dict[str, object] = {}     # name → square BGR image
        self.learned: set[str] = set()
        self._cache: dict = {}                   # (name, diameter) → [(template, mask)]

    def set(self, name: str, img_bgr, learned: bool = False) -> None:
        self.images[name] = img_bgr
        self._cache = {k: v for k, v in self._cache.items() if k[0] != name}
        if learned:
            self.learned.add(name)

    def __len__(self):
        return len(self.images)

    def _templates(self, name: str, diameter: int):
        key = (name, diameter)
        if key not in self._cache:
            src = self.images[name]
            if name not in self.learned:
                # square official portrait: the minimap mostly shows its centre
                hh, ww = src.shape[:2]
                m = int(min(hh, ww) * 0.08)
                src = src[m:hh - m, m:ww - m]
            out = []
            for sc in self.SCALES:
                d = max(7, int(round(diameter * sc)) | 1)
                tpl = cv2.resize(src, (d, d), interpolation=cv2.INTER_AREA)
                mask = np.zeros((d, d), np.uint8)
                cv2.circle(mask, (d // 2, d // 2), d // 2 - 1, 255, -1)
                out.append((tpl, mask))
            self._cache[key] = out
        return self._cache[key]

    def match(self, minimap_bgr, diameter: int):
        """For each champion: (score, x, y) of the best position."""
        res = {}
        for name in self.images:
            best = (-1.0, 0.0, 0.0)
            for tpl, mask in self._templates(name, diameter):
                if tpl.shape[0] >= minimap_bgr.shape[0] or tpl.shape[1] >= minimap_bgr.shape[1]:
                    continue
                r = cv2.matchTemplate(minimap_bgr, tpl, cv2.TM_CCOEFF_NORMED, mask=mask)
                r = np.nan_to_num(r, nan=-1.0, posinf=-1.0, neginf=-1.0)
                _, mx, _, loc = cv2.minMaxLoc(r)
                if mx > best[0]:
                    best = (float(mx), loc[0] + tpl.shape[1] / 2.0, loc[1] + tpl.shape[0] / 2.0)
            res[name] = best
        return res


def detect(minimap_bgr, cfg: VisionConfig, bank: PortraitBank | None = None) -> Detection:
    """Analyses a minimap image (BGR). Returned coordinates are fractions (0..1)."""
    h, w = minimap_bgr.shape[:2]
    if w == 0 or h == 0:
        return Detection(None, [], [])
    hsv = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    vivid = (S >= cfg.ring_s_min) & (V >= cfg.ring_v_min)
    red = vivid & ((H <= cfg.red_h_low) | (H >= cfg.red_h_high))
    blue = vivid & (H >= cfg.blue_h[0]) & (H <= cfg.blue_h[1])

    # Champion icons = circles (portrait) inside a red (enemy) or blue (ally) ring.
    # Turrets (shields) and minions (small dots) are not circles of that size.
    gray = cv2.medianBlur(cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2GRAY), 3)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=max(4, int(0.05 * w)), param1=80,
                               param2=cfg.hough_sensitivity, minRadius=max(3, int(cfg.icon_r_min * w)),
                               maxRadius=max(4, int(cfg.icon_r_max * w)))
    enemies, allies = [], []
    if circles is not None:
        rmin, rmax = cfg.icon_r_min * w, cfg.icon_r_max * w
        radii = [rmin + (rmax - rmin) * f for f in (0.2, 0.45, 0.7, 0.95)]
        step = max(1.0, 0.006 * w)
        found = []   # (coverage, x, y, side)
        for x, y, r0 in circles[0]:
            # quick filter: without some red/blue around, no need to refine
            quick_r = _ring_coverage(red, float(x), float(y), float(r0))
            quick_b = _ring_coverage(blue, float(x), float(y), float(r0))
            if max(quick_r, quick_b) < 0.25:
                continue
            cr, rx, ry, _ = _best_ring(red, float(x), float(y), radii, step) if quick_r >= 0.25 else (0.0, 0, 0, 0)
            cb, bx, by, _ = _best_ring(blue, float(x), float(y), radii, step) if quick_b >= 0.25 else (0.0, 0, 0, 0)
            if cr >= cfg.ring_coverage and cr >= cb:
                found.append((cr, rx, ry, "E"))
            elif cb >= cfg.ring_coverage:
                found.append((cb, bx, by, "A"))
        # several Hough candidates for one icon: keep the best (duplicate removal)
        found.sort(reverse=True)
        kept = []
        for c, x, y, side in found:
            if all(math.hypot(x - kx, y - ky) > rmin * 1.1 for _, kx, ky, _ in kept):
                kept.append((c, x, y, side))
        enemies = [(x, y) for _, x, y, side in kept if side == "E"]
        allies = [(x / w, y / h) for _, x, y, side in kept if side == "A"]
    names = [""] * len(enemies)

    # Portrait recognition: also finds half-hidden icons, and tells WHO is where.
    if bank is not None and len(bank):
        rmid = (cfg.icon_r_min + cfg.icon_r_max) / 2 * w
        diameter = int(round(2 * rmid * 0.8))           # portrait = inside of the ring
        hits = []
        for name, (score, x, y) in bank.match(minimap_bgr, diameter).items():
            ring = _best_ring(red, x, y, [rmid * 0.9, rmid, rmid * 1.1], max(1.0, 0.006 * w))[0]
            if score >= cfg.portrait_strong or (score >= cfg.portrait_weak and ring >= cfg.portrait_ring):
                hits.append((score, ring, x, y, name))
        hits.sort(reverse=True)
        accepted = []
        for score, ring, x, y, name in hits:
            if all(math.hypot(x - ax, y - ay) > rmid * 1.1 for _, _, ax, ay, _ in accepted):
                accepted.append((score, ring, x, y, name))
        merged_e, merged_n = [(x, y) for _, _, x, y, _ in accepted], [n for *_, n in accepted]
        for (x, y) in enemies:            # unrecognised red rings: kept (unknown champion)
            if all(math.hypot(x - ax, y - ay) > rmid * 1.2 for ax, ay in merged_e):
                merged_e.append((x, y))
                merged_n.append("")
        enemies, names = merged_e[:cfg.max_enemies], merged_n[:cfg.max_enemies]
        # learning: the real icon replaces the official portrait
        for score, ring, x, y, name in accepted:
            if score >= cfg.portrait_learn and ring >= cfg.ring_coverage and name not in bank.learned:
                rr = int(round(rmid * 0.8))
                x0, y0 = int(round(x)) - rr, int(round(y)) - rr
                if x0 >= 0 and y0 >= 0 and x0 + 2 * rr + 1 <= w and y0 + 2 * rr + 1 <= h:
                    bank.set(name, minimap_bgr[y0:y0 + 2 * rr + 1, x0:x0 + 2 * rr + 1].copy(), learned=True)
    enemies = [(x / w, y / h) for x, y in enemies]

    white = ((S <= cfg.white_s_max) & (V >= cfg.white_v_min)).astype(np.uint8) * 255
    contours, _ = cv2.findContours(white, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if not (cfg.camera_min * w <= bw <= cfg.camera_max * w and cfg.camera_min * h <= bh <= cfg.camera_max * h):
            continue
        fill = cv2.countNonZero(white[y:y + bh, x:x + bw]) / float(bw * bh)
        if fill > 0.5:          # a camera rectangle is a thin OUTLINE, not a filled blob
            continue
        if best is None or bw * bh > best[2] * best[3]:
            best = (x, y, bw, bh)
    camera = ((best[0] + best[2] / 2) / w, (best[1] + best[3] / 2) / h) if best else None
    return Detection(camera, enemies, allies, names=names)


def annotate(minimap_bgr, det: Detection, cfg: VisionConfig):
    """Debug image: camera rectangle (green), enemies (red), allies (blue), "near" radius."""
    img = minimap_bgr.copy()
    h, w = img.shape[:2]
    if det.camera:
        c = (int(det.camera[0] * w), int(det.camera[1] * h))
        cv2.circle(img, c, int(cfg.near_radius * w), (0, 255, 0), 1)
        cv2.drawMarker(img, c, (0, 255, 0), cv2.MARKER_CROSS, 10, 2)
    rad = int(cfg.icon_r_max * w)
    for i, (x, y) in enumerate(det.enemies):
        cv2.circle(img, (int(x * w), int(y * h)), rad, (0, 0, 255), 2)
        name = det.names[i] if i < len(det.names) else ""
        if name:
            cv2.putText(img, name[:6], (int(x * w) - rad, int(y * h) - rad - 2), cv2.FONT_HERSHEY_SIMPLEX,
                        0.35, (255, 255, 255), 1, cv2.LINE_AA)
    for x, y in det.allies:
        cv2.circle(img, (int(x * w), int(y * h)), rad, (255, 128, 0), 2)
    return img


def decode_obs_image(data: str):
    """OBS returns 'data:image/jpg;base64,…'."""
    raw = base64.b64decode(data.split(",", 1)[1] if "," in data else data)
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


# --------------------------------------------------------------------------- engine (thread)
class VisionRunner:
    """Reads the OBS sources regularly in a thread and keeps a short history per player.

    The score does not rely on a single image: it takes the MAX of nearby enemies over `smooth_window`
    (an icon that blinks or passes under another does not drop the score), plus a bonus
    when enemies are CLOSING IN (fight anticipation).
    """

    def __init__(self, cfg: VisionConfig, obs_args: dict | None = None, players=()):
        """players: list of PlayerConfig (OBS source, resolution, each player's own minimap)."""
        self.cfg = cfg
        self.obs_args = obs_args or {}
        self.players = {p.id: p for p in players if p.vision_source}
        self.sources = {pid: p.vision_source for pid, p in self.players.items()}
        self.bank = PortraitBank()
        self.enemy_ids: list[str] = []
        self._portraits_tried = float("-inf")   # last download attempt (retried every 60 s)
        self.latest: dict[str, Detection] = {}
        self.history: dict[str, list] = {}     # pid → [(t, near_count, min_distance)]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        if cfg.enemy_champions:
            self.set_enemies(list(cfg.enemy_champions), blocking=True)

    @staticmethod
    def available() -> bool:
        return cv2 is not None

    # ------------------------------------------------------------------ the game's champions
    def set_enemies(self, ids: list, blocking: bool = False) -> None:
        """Called by the director with the enemy champions (sent by the agent, ~3 times/s).

        Downloads again only when the list changes, or every 60 s after a failure
        (Data Dragon unreachable), never on every packet.
        """
        from .portraits import valid_ids

        ids = valid_ids(ids)
        if not ids or not self.available():
            return
        now = time.monotonic()
        same_list = ids == self.enemy_ids
        if same_list and (len(self.bank) or now - self._portraits_tried < 60.0):
            return
        self.enemy_ids, self._portraits_tried = ids, now
        if not same_list:
            log.info("Enemy champions: %s", ", ".join(ids))

        def load():
            from .portraits import ensure_portraits

            paths = ensure_portraits(ids, self.cfg.portraits_dir)
            bank = PortraitBank()
            for cid, path in paths.items():
                img = cv2.imread(str(path))
                if img is not None:
                    bank.set(cid, img)
            with self._lock:
                self.bank = bank

        if blocking:
            load()
        else:
            threading.Thread(target=load, daemon=True, name="portraits").start()

    # ------------------------------------------------------------------ processing one image
    def process(self, pid: str, frame, now: float) -> Detection | None:
        """Analyses a full game image. Returns None if the image is black (game not shown)."""
        if frame is None or float(frame.mean()) < self.cfg.black_level:
            return None
        with self._lock:
            bank = self.bank
        p = self.players.get(pid)
        roi = p.minimap if p is not None and p.minimap else self.cfg.minimap
        det = detect(crop_minimap(frame, roi), self.cfg, bank)
        det.t = now
        near = det.enemies_near(self.cfg.near_radius) if det.camera else None
        with self._lock:
            self.latest[pid] = det
            h = self.history.setdefault(pid, [])
            h.append((now, near, det.nearest_enemy()))
            keep = max(self.cfg.smooth_window, self.cfg.approach_window, self.cfg.baseline_window) + 1.0
            while h and now - h[0][0] > keep:
                h.pop(0)
        return det

    def start(self) -> bool:
        if not self.cfg.enabled or not self.sources:
            return False
        if not self.available():
            log.warning("Vision off: OpenCV missing (pip install opencv-python numpy)")
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="vision")
        self._thread.start()
        log.info("Minimap vision active on %s", self.sources)
        return True

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        import obsws_python as obs

        cl, self._warned = None, set()
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                if cl is None:
                    cl = obs.ReqClient(**self.obs_args)
                self.grab_once(cl)
            except Exception as e:
                log.warning("Vision: lost connection to OBS (%s)", e)
                cl = None
                time.sleep(2)
            time.sleep(max(0.0, self.cfg.interval - (time.monotonic() - t0)))

    def grab_once(self, cl) -> None:
        """One read of each OBS source (cl = obs-websocket client)."""
        warned = getattr(self, "_warned", set())
        self._warned = warned
        for pid, name in self.sources.items():
            p = self.players[pid]
            try:
                r = cl.get_source_screenshot(name, "jpg", p.width, p.height, 85)
            except Exception as e:
                if name not in warned:
                    log.warning("Vision: cannot read OBS source %r (%s)", name, e)
                    warned.add(name)
                continue
            self.process(pid, decode_obs_image(r.image_data), time.monotonic())

    # ------------------------------------------------------------------ read by the director
    def fresh(self, now: float) -> dict[str, Detection]:
        with self._lock:
            return {p: d for p, d in self.latest.items() if now - d.t <= self.cfg.stale_after}

    def near_count(self, pid: str, now: float) -> int:
        """Max of nearby enemies over the smoothing window (0 without a recent read)."""
        with self._lock:
            h = [e for e in self.history.get(pid, []) if now - e[0] <= self.cfg.smooth_window and e[1] is not None]
        return max((e[1] for e in h), default=0)

    def baseline(self, pid: str, now: float) -> float:
        """This player's "usual" number of nearby enemies (median over baseline_window)."""
        with self._lock:
            vals = sorted(e[1] for e in self.history.get(pid, [])
                          if now - e[0] <= self.cfg.baseline_window and e[1] is not None)
        if len(vals) < 8:          # not enough history yet: assume nothing
            return 0.0
        return float(vals[len(vals) // 2])

    def extra_near(self, pid: str, now: float) -> float:
        """Nearby enemies ABOVE the usual count (≥ 0)."""
        return max(0.0, self.near_count(pid, now) - self.baseline(pid, now))

    def approaching(self, pid: str, now: float) -> bool:
        """One MORE enemy is coming near the player, above their usual count (gank, roam, engage).

        The lane opponent moving back and forth does not count: the usual count must be exceeded.
        """
        with self._lock:
            h = [e for e in self.history.get(pid, []) if now - e[0] <= self.cfg.approach_window]
        if len(h) < 3:
            return False
        n0, n1 = h[0][1], h[-1][1]
        return n0 is not None and n1 is not None and n1 > n0 and n1 > self.baseline(pid, now)

    def bonus(self, pid: str, now: float) -> float:
        if pid not in self.fresh(now):
            return 0.0
        b = min(self.extra_near(pid, now), self.cfg.max_enemies) * self.cfg.w_enemy_near
        if self.approaching(pid, now):
            b += self.cfg.w_enemy_approach
        return b

    def together_pairs(self, now: float) -> list[tuple[str, str]]:
        """Pairs of players whose cameras are in the same place on the map (same fight)."""
        f = {p: d for p, d in self.fresh(now).items() if d.camera}
        ids = sorted(f)
        out = []
        for i, p in enumerate(ids):
            for q in ids[i + 1:]:
                (x1, y1), (x2, y2) = f[p].camera, f[q].camera
                if math.hypot(x1 - x2, y1 - y2) <= self.cfg.together_dist:
                    out.append((p, q))
        return out

    def together(self, now: float) -> bool:
        return bool(self.together_pairs(now))
