"""Directeur : tourne sur le PC qui diffuse (celui d'OBS). Reçoit les scores et pilote OBS.

    python -m lolauto.director              # pilote OBS pour de vrai
    python -m lolauto.director --dry-run    # affiche seulement les décisions (test sans OBS)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import socket
import time

from . import config as cfgmod
from . import logs
from .switcher import SwitchConfig, Switcher

log = logging.getLogger("director")


# --------------------------------------------------------------------------- contrôleurs
class DryRunController:
    """Simule OBS : se souvient de la scène « courante », n'envoie rien."""

    def __init__(self, initial: str | None = None):
        self.current = initial

    def get_current(self) -> str | None:
        return self.current

    def set(self, scene: str) -> bool:
        self.current = scene
        return True

    def scene_names(self) -> list[str] | None:
        return None

    def set_split_pair(self, split_scene: str, left: str, right: str) -> bool:
        self.split = (left, right)
        return True


class ObsController:
    """Pilote OBS via obs-websocket v5 (intégré à OBS ≥ 28). Se reconnecte tout seul."""

    def __init__(self, host: str, port: int, password: str, timeout: float = 3.0):
        self.args = dict(host=host, port=port, password=password, timeout=timeout)
        self._cl = None
        self._next_try = 0.0

    def _client(self):
        if self._cl is None and time.monotonic() >= self._next_try:
            try:
                import obsws_python as obs

                self._cl = obs.ReqClient(**self.args)
                log.info("Connecté à OBS %s", self._cl.get_version().obs_version)
            except Exception as e:
                self._cl = None
                self._next_try = time.monotonic() + 2.0
                log.warning("OBS injoignable (%s) — nouvel essai dans 2 s", e)
        return self._cl

    def _lost(self, e: Exception) -> None:
        log.warning("Connexion OBS perdue (%s)", e)
        self._cl = None
        self._next_try = time.monotonic() + 2.0

    def get_current(self) -> str | None:
        cl = self._client()
        if cl is None:
            return None
        try:
            r = cl.get_current_program_scene()
            return getattr(r, "current_program_scene_name", None) or getattr(r, "scene_name", None)
        except Exception as e:
            self._lost(e)
            return None

    def set(self, scene: str) -> bool:
        cl = self._client()
        if cl is None:
            return False
        try:
            cl.set_current_program_scene(scene)
            return True
        except Exception as e:
            log.warning("Impossible d'afficher la scène %r (%s)", scene, e)
            return False

    def scene_names(self) -> list[str] | None:
        cl = self._client()
        if cl is None:
            return None
        try:
            return [s["sceneName"] for s in cl.get_scene_list().scenes]
        except Exception as e:
            self._lost(e)
            return None

    def set_split_pair(self, split_scene: str, left: str, right: str) -> bool:
        """Écran partagé dynamique : la scène SPLIT contient chaque scène POV_x deux fois (moitié gauche
        et moitié droite, cf. `python -m lolauto obs-setup --split`). On n'affiche que `left` à gauche et
        `right` à droite. Une scène SPLIT faite à la main (sans scènes imbriquées) est laissée telle quelle.
        """
        cl = self._client()
        if cl is None:
            return False
        try:
            items = cl.get_scene_item_list(split_scene).scene_items
            nested = [it for it in items if it.get("sourceType") == "OBS_SOURCE_TYPE_SCENE"]
            if not nested:
                return True
            canvas = cl.get_video_settings().base_width
            for it in nested:
                tr = it.get("sceneItemTransform") or {}
                center = float(tr.get("positionX", 0.0)) + float(tr.get("width", 0.0)) / 2
                side_left = center < canvas / 2
                want = it["sourceName"] == (left if side_left else right)
                if bool(it.get("sceneItemEnabled")) != want:
                    cl.set_scene_item_enabled(split_scene, it["sceneItemId"], want)
            return True
        except Exception as e:
            log.warning("Impossible de préparer l'écran partagé (%s)", e)
            return False


# --------------------------------------------------------------------------- cerveau
class Director:
    POLL_EVERY = 0.5  # fréquence de lecture de la scène OBS courante (détection du manuel)

    def __init__(self, cfg: SwitchConfig, scenes: dict[str, str], controller, start: str | None = None,
                 vision=None):
        self.scenes = {k: v for k, v in scenes.items() if v}
        start = start or next(k for k in self.scenes if k != "SPLIT")
        if "SPLIT" not in self.scenes and cfg.split_enabled:
            cfg = dataclasses.replace(cfg, split_enabled=False)
        self.cfg = cfg
        self.ctl = controller
        self.sw = Switcher(cfg, start)
        self.pause_until = 0.0
        self.external = False   # OBS est sur une scène qu'on ne gère pas (BRB, intro…)
        self._synced = False
        self._last_poll = float("-inf")
        self._retry_after = 0.0
        self._validated = False
        self.vision = vision    # VisionRunner (ou tout objet avec bonus(pid, now) et together(now))

    def handle(self, msg: dict, now: float) -> None:
        score = msg.get("score") if msg.get("in_game") else None
        if self.vision is not None and msg.get("enemies") and hasattr(self.vision, "set_enemies"):
            self.vision.set_enemies(msg["enemies"])        # portraits des champions adverses
        if score is not None and self.vision is not None and not msg.get("dead") and score >= 0:
            score += self.vision.bonus(msg["id"], now)   # ennemis proches sur la minimap
        self.sw.feed(msg["id"], score, msg.get("dead", False), now, msg.get("ev") or ())

    def _key_of(self, scene: str | None) -> str | None:
        return next((k for k, v in self.scenes.items() if v == scene), None)

    def _validate(self) -> None:
        names = self.ctl.scene_names()
        if names is None:
            return
        self._validated = True
        for key, scene in list(self.scenes.items()):
            if scene not in names:
                log.warning("Scène OBS introuvable : %r (%s)", scene, key)
                if key == "SPLIT":
                    del self.scenes["SPLIT"]
                    self.sw.cfg = dataclasses.replace(self.sw.cfg, split_enabled=False)
                    log.warning("→ mode SPLIT désactivé")

    def _sync(self, now: float) -> None:
        cur = self.ctl.get_current()
        if cur is None:
            return
        key = self._key_of(cur)
        if key is None:
            if not self.external:
                log.info("Scène OBS %r non gérée : l'automatique se met en pause", cur)
            self.external = True
            return
        if self.external or not self._synced:
            self.sw.apply(key, now)           # on s'aligne sur OBS, sans pause
            self.sw.last_switch = float("-inf")
            self.external = False
            self._synced = True
            return
        if cur != self.scenes.get(self.sw.current):
            log.info("Changement MANUEL détecté (%s) : auto en pause %.0f s", cur, self.cfg.manual_pause)
            self.sw.apply(key, now)
            self.pause_until = now + self.cfg.manual_pause

    def step(self, now: float):
        """Un tour de boucle. Retourne (cible, scène) si un changement a eu lieu."""
        if not self._validated:
            self._validate()
        if self.vision is not None:
            for pair in self.vision.together_pairs(now):
                self.sw.mark_together(now, pair)
        if now - self._last_poll >= self.POLL_EVERY:
            self._last_poll = now
            self._sync(now)
        if not self._synced or self.external or now < self.pause_until or now < self._retry_after:
            return None
        target = self.sw.decide(now)
        scene = self.scenes.get(target) if target else None
        if not scene:
            return None
        pair = None
        if target == "SPLIT":
            pair = self.sw.pending_pair
            if pair is None or any(p not in self.scenes for p in pair):
                return None
            setter = getattr(self.ctl, "set_split_pair", None)
            if setter and not setter(scene, self.scenes[pair[0]], self.scenes[pair[1]]):
                self._retry_after = now + 2.0
                return None
        if self.ctl.set(scene):
            self.sw.apply(target, now, pair)
            return (target, f"{scene} {pair[0]}+{pair[1]}") if pair else (target, scene)
        self._retry_after = now + 2.0
        return None


# --------------------------------------------------------------------------- programme
def _fmt(live: dict, ids) -> str:
    return " ".join(f"{p}={live.get(p)}" for p in ids)


def parse_packet(raw: bytes, token: str, ids=("A", "B", "C", "D", "E")) -> dict | None:
    """Paquet UDP d'un agent → message, ou None s'il est invalide / d'un autre réseau (mauvais jeton)."""
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(msg, dict) or msg.get("v") != 1 or msg.get("id") not in ids:
        return None
    if msg.get("token", "") != token:
        return None
    score = msg.get("score")
    if score is not None and not isinstance(score, (int, float)):
        return None
    return msg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Directeur OBS automatique")
    ap.add_argument("--config", help="chemin de config.toml")
    ap.add_argument("--dry-run", action="store_true", help="n'envoie rien à OBS, affiche les décisions")
    ap.add_argument("--listen-port", type=int, help="remplace [network].director_port")
    ap.add_argument("-v", "--verbose", action="store_true", help="affiche les scores chaque seconde")
    ap.add_argument("--log-file", help="écrit aussi le journal dans ce fichier (ex. logs/director.log)")
    args = ap.parse_args(argv)
    logs.setup(args.verbose, args.log_file)

    cfg = cfgmod.load(args.config)
    port = args.listen_port or cfg.network.director_port
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(0.1)

    scenes = cfg.scenes
    first = cfg.players[0].id
    ctl = (DryRunController(scenes[first]) if args.dry_run else
           ObsController(cfg.obs.host, cfg.obs.port, cfg.obs.password))
    vision = None
    if not args.dry_run and cfg.vision.enabled:
        from .vision import VisionRunner

        vision = VisionRunner(cfg.vision, dict(host=cfg.obs.host, port=cfg.obs.port,
                                               password=cfg.obs.password, timeout=3), cfg.players)
        if not vision.start():
            vision = None
    director = Director(cfg.switching, scenes, ctl, vision=vision)
    log.info("Directeur prêt (UDP :%d)%s — %d joueur(s) — scènes : %s", port, " [DRY-RUN]" if args.dry_run else "",
             len(cfg.players), scenes)

    last_status = 0.0
    while True:
        try:
            raw, _addr = sock.recvfrom(4096)
        except socket.timeout:
            raw = None
        now = time.monotonic()
        msg = parse_packet(raw, cfg.network.token, cfg.player_ids) if raw else None
        if msg:
            director.handle(msg, now)

        done = director.step(now)
        live = director.sw.live(now)
        if done:
            target, scene = done
            log.info("SWITCH → %s (%s) | %s", target, scene, _fmt(live, cfg.player_ids))
        if now - last_status >= 1.0:
            last_status = now
            extra = ""
            if vision is not None:
                f = vision.fresh(now)
                extra = " | vision " + " ".join(
                    f"{p}:cam={'oui' if d.camera else 'non'},ennemis={len(d.enemies)},proches={vision.near_count(p, now)}"
                    f"{',approche' if vision.approaching(p, now) else ''}"
                    for p, d in sorted(f.items()))
                pairs = vision.together_pairs(now)
                if pairs:
                    extra += " ENSEMBLE " + ",".join(f"{p}+{q}" for p, q in pairs)
            log.debug("état=%s %s%s", director.sw.current, _fmt(live, cfg.player_ids), extra)


if __name__ == "__main__":
    raise SystemExit(main())
