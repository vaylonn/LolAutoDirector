"""Lecture de la minimap (OpenCV) à partir des images de OBS.

Pourquoi passer par OBS : le directeur y a déjà accès, et OBS voit les DEUX POV (ta capture de jeu et
la source NDI du Mac). Pas besoin d'installer quoi que ce soit sur le Mac pour la vision.

Ce qui est détecté sur chaque minimap :
- le rectangle blanc de la caméra (= là où le joueur regarde, en pratique autour de son champion) ;
- les icônes de champions ennemis (anneau rouge) et alliés (anneau bleu).

On en tire :
- un bonus « danger / anticipation » = ennemis proches du rectangle caméra AU-DELÀ du normal de ce
  joueur (le vis-à-vis de lane ne compte pas), + un bonus quand un ennemi de plus arrive ;
- « ensemble » = les rectangles caméra de A et de B sont proches → même combat (pour split_mode).

⚠ Les seuils de couleur et de taille sont à CALIBRER sur ta configuration (résolution, taille de minimap) :
voir `python -m lolauto vision-debug`.
"""

from __future__ import annotations

import base64
import logging
import math
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("vision")

try:  # OpenCV est optionnel : sans lui, la vision est simplement désactivée
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None
    np = None


@dataclass
class VisionConfig:
    enabled: bool = True
    interval: float = 0.25             # secondes entre deux lectures (par joueur)
    # Zone de la minimap en fractions de l'image [x0, y0, x1, y1] (coin bas-droit par défaut)
    minimap: list = field(default_factory=lambda: [0.861, 0.753, 0.994, 0.988])
    # Couleurs (HSV OpenCV : H 0-180, S/V 0-255)
    red_h_low: int = 8                 # rouge = H <= red_h_low ou H >= red_h_high
    red_h_high: int = 170
    blue_h: list = field(default_factory=lambda: [95, 125])
    ring_s_min: int = 110
    ring_v_min: int = 110
    white_s_max: int = 45
    white_v_min: int = 200
    # Icônes de champions : cercles de rayon icon_r_min..icon_r_max (fraction de la largeur de minimap)
    icon_r_min: float = 0.032
    icon_r_max: float = 0.050
    ring_coverage: float = 0.8         # part du tour du cercle qui doit être de la couleur (rouge/bleu)
    hough_sensitivity: int = 10        # plus bas = plus de cercles candidats (param2 de HoughCircles)
    camera_min: float = 0.12
    camera_max: float = 0.55
    near_radius: float = 0.20          # « proche » = à moins de 20 % de minimap du centre caméra
    together_dist: float = 0.15        # caméras de A et B à moins de 15 % → même combat
    w_enemy_near: float = 1.2          # bonus par ennemi proche…
    max_enemies: int = 5               # …plafonné
    stale_after: float = 1.5           # une lecture plus vieille est ignorée
    # Reconnaissance des portraits (champions de la partie, envoyés par l'agent via l'API de LoL)
    enemy_champions: list = field(default_factory=list)  # forcer la liste (rediff), ex. ["Ahri", "Zed"]
    portraits_dir: str = "data/portraits"
    portrait_strong: float = 0.80      # ressemblance suffisante seule…
    portrait_weak: float = 0.50        # …ou ressemblance moyenne + anneau rouge visible
    portrait_ring: float = 0.45
    portrait_learn: float = 0.65       # au-delà (et anneau net), le portrait est réappris sur la minimap
    # Stabilité / anticipation
    smooth_window: float = 0.75        # on garde le max d'ennemis proches sur cette durée (icônes qui clignotent)
    w_enemy_approach: float = 0.8      # bonus si des ennemis se rapprochent (anticipation)
    approach_window: float = 1.5
    # « Normal » de chaque joueur : médiane des ennemis proches sur baseline_window secondes.
    # En lane, le bot a 2 ennemis en face en permanence, le top 1 : ce n'est pas de l'action.
    # Seuls les ennemis AU-DELÀ de ce normal comptent (gank, roam, teamfight).
    baseline_window: float = 60.0
    black_level: float = 4.0           # image plus sombre = jeu pas affiché (Alt+Tab, chargement) → ignorée

    def __post_init__(self):
        if len(self.minimap) != 4 or not (0 <= self.minimap[0] < self.minimap[2] <= 1
                                         and 0 <= self.minimap[1] < self.minimap[3] <= 1):
            raise ValueError(f"[vision].minimap doit être [x0, y0, x1, y1] entre 0 et 1, pas {self.minimap}")
        if self.interval <= 0:
            raise ValueError("[vision].interval doit être > 0")


@dataclass
class Detection:
    camera: tuple | None               # centre du rectangle caméra (x, y) en fractions de minimap
    enemies: list                      # [(x, y), …]
    allies: list
    t: float = 0.0
    names: list = field(default_factory=list)  # champion reconnu pour chaque ennemi ("" = inconnu)

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


# --------------------------------------------------------------------------- détection pure
def crop_minimap(frame, roi):
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = roi
    return frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


# Points d'échantillonnage de l'anneau (rayon 1) : 16 secteurs × 4 angles × 4 rayons (0.82r..1.12r)
if np is not None:
    _ANG = np.radians(np.repeat(np.arange(16), 4) * 22.5 + np.tile([2.8, 8.4, 14.1, 19.7], 16))
    _F = np.repeat([0.82, 0.92, 1.02, 1.12], 64)
    _RCOS = np.tile(np.cos(_ANG), 4) * _F
    _RSIN = np.tile(np.sin(_ANG), 4) * _F
    _SECT4 = np.tile(np.repeat(np.arange(16), 4), 4)


def _ring_coverage(mask, x, y, r):
    """Part des 16 secteurs angulaires de l'anneau (0.8r..1.15r) où la couleur est présente.

    Les secteurs qui sortent de l'image (icône coupée par le bord) sont ignorés.
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
    """Hough donne un centre/rayon approximatifs : on cherche autour la position qui « colle » le mieux."""
    best = (0.0, x, y, radii[0])
    for dx in (-step, 0.0, step):
        for dy in (-step, 0.0, step):
            for r in radii:
                c = _ring_coverage(mask, x + dx, y + dy, r)
                if c > best[0]:
                    best = (c, x + dx, y + dy, r)
    return best


class PortraitBank:
    """Portraits des champions ennemis, cherchés tels quels sur la minimap (corrélation normalisée).

    Les portraits viennent de Data Dragon (images officielles Riot). Dès qu'un champion est reconnu
    avec certitude, son portrait est REMPLACÉ par l'icône vue sur la minimap (rendu exact du jeu) :
    la reconnaissance s'améliore toute seule au fil de la partie.
    """

    SCALES = (0.85, 1.0, 1.15)

    def __init__(self):
        self.images: dict[str, object] = {}     # nom → image BGR carrée
        self.learned: set[str] = set()
        self._cache: dict = {}                   # (nom, diamètre) → [(gabarit, masque)]

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
                # portrait officiel carré : la minimap en montre surtout le centre
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
        """Pour chaque champion : (score, x, y) de la meilleure position."""
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
    """Analyse une image de minimap (BGR). Coordonnées renvoyées en fractions (0..1)."""
    h, w = minimap_bgr.shape[:2]
    if w == 0 or h == 0:
        return Detection(None, [], [])
    hsv = cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    vivid = (S >= cfg.ring_s_min) & (V >= cfg.ring_v_min)
    red = vivid & ((H <= cfg.red_h_low) | (H >= cfg.red_h_high))
    blue = vivid & (H >= cfg.blue_h[0]) & (H <= cfg.blue_h[1])

    # Icônes de champions = cercles (portrait) entourés d'un anneau rouge (ennemi) ou bleu (allié).
    # Les tourelles (boucliers) et les sbires (petits points) ne sont pas des cercles de cette taille.
    gray = cv2.medianBlur(cv2.cvtColor(minimap_bgr, cv2.COLOR_BGR2GRAY), 3)
    circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, dp=1, minDist=max(4, int(0.05 * w)), param1=80,
                               param2=cfg.hough_sensitivity, minRadius=max(3, int(cfg.icon_r_min * w)),
                               maxRadius=max(4, int(cfg.icon_r_max * w)))
    enemies, allies = [], []
    if circles is not None:
        rmin, rmax = cfg.icon_r_min * w, cfg.icon_r_max * w
        radii = [rmin + (rmax - rmin) * f for f in (0.2, 0.45, 0.7, 0.95)]
        step = max(1.0, 0.006 * w)
        found = []   # (couverture, x, y, camp)
        for x, y, r0 in circles[0]:
            # tri rapide : sans un minimum de rouge/bleu autour, inutile d'affiner
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
        # plusieurs candidats Hough pour la même icône : on garde le meilleur (suppression des doublons)
        found.sort(reverse=True)
        kept = []
        for c, x, y, side in found:
            if all(math.hypot(x - kx, y - ky) > rmin * 1.1 for _, kx, ky, _ in kept):
                kept.append((c, x, y, side))
        enemies = [(x, y) for _, x, y, side in kept if side == "E"]
        allies = [(x / w, y / h) for _, x, y, side in kept if side == "A"]
    names = [""] * len(enemies)

    # Reconnaissance des portraits : retrouve aussi les icônes à moitié cachées, et dit QUI est où.
    if bank is not None and len(bank):
        rmid = (cfg.icon_r_min + cfg.icon_r_max) / 2 * w
        diameter = int(round(2 * rmid * 0.8))           # portrait = intérieur de l'anneau
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
        for (x, y) in enemies:            # anneaux rouges non reconnus : gardés (champion inconnu)
            if all(math.hypot(x - ax, y - ay) > rmid * 1.2 for ax, ay in merged_e):
                merged_e.append((x, y))
                merged_n.append("")
        enemies, names = merged_e[:cfg.max_enemies], merged_n[:cfg.max_enemies]
        # apprentissage : l'icône réelle remplace le portrait officiel
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
        if fill > 0.5:          # un rectangle de caméra est un CONTOUR fin, pas une tache pleine
            continue
        if best is None or bw * bh > best[2] * best[3]:
            best = (x, y, bw, bh)
    camera = ((best[0] + best[2] / 2) / w, (best[1] + best[3] / 2) / h) if best else None
    return Detection(camera, enemies, allies, names=names)


def annotate(minimap_bgr, det: Detection, cfg: VisionConfig):
    """Image de debug : rectangle caméra (vert), ennemis (rouge), alliés (bleu), rayon « proche »."""
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
    """OBS renvoie 'data:image/jpg;base64,…'."""
    raw = base64.b64decode(data.split(",", 1)[1] if "," in data else data)
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


# --------------------------------------------------------------------------- moteur (thread)
class VisionRunner:
    """Lit régulièrement les sources OBS dans un thread et garde un petit historique par joueur.

    Le score n'utilise pas une seule image : on prend le MAX d'ennemis proches sur `smooth_window`
    (une icône qui clignote ou passe sous une autre ne fait pas retomber le score), plus un bonus
    quand les ennemis se RAPPROCHENT (anticipation du combat).
    """

    def __init__(self, cfg: VisionConfig, obs_args: dict | None = None, players=()):
        """players : liste de PlayerConfig (source OBS, résolution, minimap propre à chaque joueur)."""
        self.cfg = cfg
        self.obs_args = obs_args or {}
        self.players = {p.id: p for p in players if p.vision_source}
        self.sources = {pid: p.vision_source for pid, p in self.players.items()}
        self.bank = PortraitBank()
        self.enemy_ids: list[str] = []
        self._portraits_tried = float("-inf")   # dernier essai de téléchargement (relance toutes les 60 s)
        self.latest: dict[str, Detection] = {}
        self.history: dict[str, list] = {}     # pid → [(t, nb_proches, distance_min)]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        if cfg.enemy_champions:
            self.set_enemies(list(cfg.enemy_champions), blocking=True)

    @staticmethod
    def available() -> bool:
        return cv2 is not None

    # ------------------------------------------------------------------ champions de la partie
    def set_enemies(self, ids: list, blocking: bool = False) -> None:
        """Appelé par le directeur avec les champions adverses (envoyés par l'agent, ~3 fois/s).

        Ne relance le téléchargement que si la liste change, ou toutes les 60 s s'il a échoué
        (Data Dragon injoignable) — jamais à chaque paquet.
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
            log.info("Champions adverses : %s", ", ".join(ids))

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

    # ------------------------------------------------------------------ traitement d'une image
    def process(self, pid: str, frame, now: float) -> Detection | None:
        """Analyse une image complète du jeu. Renvoie None si l'image est noire (jeu pas affiché)."""
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
            log.warning("Vision désactivée : OpenCV absent (pip install opencv-python numpy)")
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="vision")
        self._thread.start()
        log.info("Vision minimap active sur %s", self.sources)
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
                log.warning("Vision : connexion OBS perdue (%s)", e)
                cl = None
                time.sleep(2)
            time.sleep(max(0.0, self.cfg.interval - (time.monotonic() - t0)))

    def grab_once(self, cl) -> None:
        """Une lecture de chaque source OBS (cl = client obs-websocket)."""
        warned = getattr(self, "_warned", set())
        self._warned = warned
        for pid, name in self.sources.items():
            p = self.players[pid]
            try:
                r = cl.get_source_screenshot(name, "jpg", p.width, p.height, 85)
            except Exception as e:
                if name not in warned:
                    log.warning("Vision : source OBS %r illisible (%s)", name, e)
                    warned.add(name)
                continue
            self.process(pid, decode_obs_image(r.image_data), time.monotonic())

    # ------------------------------------------------------------------ lecture par le directeur
    def fresh(self, now: float) -> dict[str, Detection]:
        with self._lock:
            return {p: d for p, d in self.latest.items() if now - d.t <= self.cfg.stale_after}

    def near_count(self, pid: str, now: float) -> int:
        """Max d'ennemis proches sur la fenêtre de lissage (0 si pas de lecture récente)."""
        with self._lock:
            h = [e for e in self.history.get(pid, []) if now - e[0] <= self.cfg.smooth_window and e[1] is not None]
        return max((e[1] for e in h), default=0)

    def baseline(self, pid: str, now: float) -> float:
        """Nombre d'ennemis proches « habituel » pour ce joueur (médiane sur baseline_window)."""
        with self._lock:
            vals = sorted(e[1] for e in self.history.get(pid, [])
                          if now - e[0] <= self.cfg.baseline_window and e[1] is not None)
        if len(vals) < 8:          # pas encore assez d'historique : on ne suppose rien
            return 0.0
        return float(vals[len(vals) // 2])

    def extra_near(self, pid: str, now: float) -> float:
        """Ennemis proches EN PLUS de l'habituel (≥ 0)."""
        return max(0.0, self.near_count(pid, now) - self.baseline(pid, now))

    def approaching(self, pid: str, now: float) -> bool:
        """Un ennemi de PLUS arrive près du joueur, au-delà de son habituel (gank, roam, engage).

        Les va-et-vient du vis-à-vis de lane ne comptent pas : il faut dépasser le « normal ».
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
        """Paires de joueurs dont les caméras sont au même endroit sur la carte (même combat)."""
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
