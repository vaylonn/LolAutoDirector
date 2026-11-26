"""App model: settings and one-click start (no UI code here; the interface lives in ui.py).

    python -m lolauto            (or LoLAutoDirector.exe)

- "This PC": role (streaming PC = OBS + director, or player PC = agent only) and player letter.
- "Players": 2 to 5 players, OBS scene, video source read by the vision, resolution of each.
- "OBS", "Directing", "Vision": the config.toml settings.

THIS PC's role and letter are kept in `this_pc.json` (config.toml is the same on every PC).
"""

from __future__ import annotations

import json
import os
import queue
import re
import socket
import subprocess
import threading
from dataclasses import dataclass, field

from . import config as cfgmod
from . import config_writer
from .config import PLAYER_IDS, Config, PlayerConfig
from .paths import BASE, cli

RESOLUTIONS = ["1280x720", "1600x900", "1920x1080", "2560x1080", "2560x1440", "3440x1440", "3840x2160"]
ROLES = {"streamer": "Streaming PC (OBS + director + my agent)", "player": "Player PC (my agent only)"}
LEGACY_ROLES = {"diffuseur": "streamer", "joueur": "player"}   # values written by earlier versions
LOCAL_FILE = BASE / "this_pc.json"
LEGACY_LOCAL_FILE = BASE / "ce_pc.json"
CONFIG_FILE = BASE / "config.toml"


# =========================================================================== model (no UI)
@dataclass
class LocalSettings:
    role: str = "streamer"
    player_id: str = "A"
    verbose: bool = False

    @classmethod
    def load(cls, path=None) -> "LocalSettings":
        if path is None:
            path = LOCAL_FILE if LOCAL_FILE.exists() or not LEGACY_LOCAL_FILE.exists() else LEGACY_LOCAL_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            s = cls(**{k: v for k, v in data.items() if k in ("role", "player_id", "verbose")})
        except (OSError, ValueError, TypeError):
            return cls()
        s.role = LEGACY_ROLES.get(s.role, s.role)
        if s.role not in ROLES:
            s.role = "streamer"
        if s.player_id not in PLAYER_IDS:
            s.player_id = "A"
        return s

    def save(self, path=LOCAL_FILE) -> None:
        path.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")


def parse_resolution(text: str) -> tuple[int, int]:
    """'2560x1440' (or '2560 × 1440') → (2560, 1440)."""
    t = text.lower().replace("×", "x").replace(" ", "")
    try:
        w, h = (int(v) for v in t.split("x"))
    except ValueError:
        raise ValueError(f"invalid resolution: {text!r} (expected e.g. 1920x1080)") from None
    if w < 320 or h < 240:
        raise ValueError(f"resolution too small: {text!r}")
    return w, h


def local_ip() -> str:
    """This PC's IP on the local network (nothing is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def load_config() -> Config:
    try:
        return cfgmod.load(CONFIG_FILE)
    except FileNotFoundError:
        return Config()


def build_commands(cfg: Config, local: LocalSettings) -> list[tuple[str, list[str]]]:
    """Programs to start on THIS PC for its role."""
    v = ["-v"] if local.verbose else []
    pid = local.player_id
    if local.role == "streamer":
        return [
            # -v: one state line per second, read by the app for the live camera wall
            ("Director", cli("director", "--log-file", str(BASE / "logs" / "director.log"), "-v")),
            (f"Agent {pid}", cli("agent", "--id", pid, "--director-host", "127.0.0.1",
                                 "--log-file", str(BASE / "logs" / f"agent_{pid}.log"), *v)),
        ]
    return [(f"Agent {pid}", cli("agent", "--id", pid, "--log-file", str(BASE / "logs" / f"agent_{pid}.log"), *v))]


def check_local(cfg: Config, local: LocalSettings) -> list[str]:
    """Warnings before starting (not blocking)."""
    warn = []
    if local.player_id not in cfg.player_ids:
        warn.append(f"This PC's player {local.player_id} is not in the player list.")
    if local.role == "player" and cfg.network.director_host in ("127.0.0.1", "localhost"):
        warn.append("Player PC: \"Streaming PC IP\" is 127.0.0.1. Enter the IP of the PC running OBS.")
    if not cfg.network.token:
        warn.append("No network token: any PC on the network can send scores.")
    return warn


@dataclass
class ProcessGroup:
    """Programs started by the app; their output goes to a queue (for display)."""
    lines: "queue.Queue[str]" = field(default_factory=queue.Queue)
    procs: list = field(default_factory=list)

    def start(self, name: str, argv: list[str]) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        p = subprocess.Popen(argv, cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=flags, env=env)
        self.procs.append((name, p))

        def pump():
            for line in p.stdout:
                self.lines.put(f"[{name}] {line.rstrip()}")
            self.lines.put(f"[{name}] stopped (exit code {p.wait()})")

        threading.Thread(target=pump, daemon=True).start()

    def running(self) -> bool:
        return any(p.poll() is None for _, p in self.procs)

    def stop(self) -> None:
        for _, p in self.procs:
            if p.poll() is None:
                p.terminate()
        for _, p in self.procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs.clear()


# =========================================================================== form → config
SWITCH_FIELDS = ("min_hold", "margin", "fast_min_hold", "fast_margin", "manual_pause", "split_threshold")


def _num(values: dict, key: str, kind=float):
    raw = str(values[key]).strip().replace(",", ".")
    try:
        return int(float(raw)) if kind is int else float(raw)
    except ValueError:
        raise ValueError(f"Invalid value for \"{key}\": {raw!r}") from None


def config_values(cfg: Config, local: LocalSettings) -> dict:
    """Config → flat values to fill a form (strings and booleans)."""
    v = {"local.role": local.role, "local.player_id": local.player_id, "local.verbose": local.verbose}
    for k in ("director_host", "director_port", "token"):
        v[f"network.{k}"] = str(getattr(cfg.network, k))
    for k in ("host", "port", "password", "scene_split"):
        v[f"obs.{k}"] = str(getattr(cfg.obs, k))
    for pid in PLAYER_IDS:
        p = cfg.player(pid)
        v[f"p.{pid}.on"] = p is not None
        v[f"p.{pid}.name"] = p.name if p else ""
        v[f"p.{pid}.scene"] = p.scene if p else f"POV_{pid}"
        v[f"p.{pid}.vision_source"] = p.vision_source if p else ""
        v[f"p.{pid}.res"] = f"{p.width}x{p.height}" if p else "1920x1080"
    for k in SWITCH_FIELDS:
        v[f"switching.{k}"] = str(getattr(cfg.switching, k))
    v["switching.split_enabled"] = cfg.switching.split_enabled
    v["switching.split_mode"] = cfg.switching.split_mode
    v["vision.enabled"] = cfg.vision.enabled
    v["vision.interval"] = str(cfg.vision.interval)
    v["vision.w_enemy_near"] = str(cfg.vision.w_enemy_near)
    for i, x in enumerate(cfg.vision.minimap):
        v[f"vision.minimap.{i}"] = str(x)
    return v


def values_to_config(base: Config, values: dict) -> tuple[Config, LocalSettings]:
    """Form values → (Config, LocalSettings). Raises ValueError with a clear message."""
    import copy
    import dataclasses

    cfg = copy.deepcopy(base)
    cfg.network = dataclasses.replace(cfg.network, director_host=str(values["network.director_host"]).strip(),
                                      director_port=_num(values, "network.director_port", int),
                                      token=str(values["network.token"]).strip())
    cfg.obs = dataclasses.replace(cfg.obs, host=str(values["obs.host"]).strip(), port=_num(values, "obs.port", int),
                                  password=str(values["obs.password"]),
                                  scene_split=str(values["obs.scene_split"]).strip())
    players = []
    for pid in PLAYER_IDS:
        if not values[f"p.{pid}.on"]:
            continue
        w, h = parse_resolution(str(values[f"p.{pid}.res"]))
        old = cfg.player(pid)
        players.append(PlayerConfig(pid, name=str(values[f"p.{pid}.name"]).strip(),
                                    scene=str(values[f"p.{pid}.scene"]).strip(),
                                    vision_source=str(values[f"p.{pid}.vision_source"]).strip(),
                                    width=w, height=h, minimap=list(old.minimap) if old else []))
    if not players:
        raise ValueError("Turn on at least one player (Players page).")
    cfg.players = players
    cfg.switching = dataclasses.replace(
        cfg.switching, **{k: _num(values, f"switching.{k}") for k in SWITCH_FIELDS},
        split_enabled=bool(values["switching.split_enabled"]), split_mode=str(values["switching.split_mode"]))
    cfg.vision = dataclasses.replace(cfg.vision, enabled=bool(values["vision.enabled"]),
                                     interval=_num(values, "vision.interval"),
                                     w_enemy_near=_num(values, "vision.w_enemy_near"),
                                     minimap=[_num(values, f"vision.minimap.{i}") for i in range(4)])
    cfgmod.loads(config_writer.dumps(cfg))          # full validation (same rules as the file)
    local = LocalSettings(role=str(values["local.role"]), player_id=str(values["local.player_id"]),
                          verbose=bool(values["local.verbose"]))
    return cfg, local


# =========================================================================== live state (read from the logs)
_SWITCH = re.compile(r"SWITCH → (\w+) \(([^)]*)\) \| (.*)")
_STATE = re.compile(r"state=(\S+) (.*?)(?: \| vision.*)?$")
_SCORE = re.compile(r"\b([A-E])=(-?[\d.]+|None)")


def _scores(text: str) -> dict:
    return {p: (None if v == "None" else float(v)) for p, v in _SCORE.findall(text)}


def parse_line(line: str) -> dict | None:
    """Director or agent log line → event for the live display."""
    m = _SWITCH.search(line)
    if m:
        target, scene = m.group(1), m.group(2)
        on_air = scene.split()[1].split("+") if target == "SPLIT" and " " in scene else [target]
        return {"kind": "switch", "target": target, "scene": scene, "on_air": on_air, "scores": _scores(m.group(3))}
    m = _STATE.search(line)
    if m:
        cur = m.group(1)
        return {"kind": "state", "current": cur, "scores": _scores(m.group(2))}
    if "Game detected" in line:
        m = re.search(r"\[Agent ([A-E])\]", line)
        return {"kind": "in_game", "player": m.group(1) if m else None}
    if "MANUAL switch" in line:
        return {"kind": "manual"}
    if "Connected to OBS" in line:
        return {"kind": "obs", "ok": True}
    if "OBS unreachable" in line:
        return {"kind": "obs", "ok": False}
    return None


def is_noise(line: str) -> bool:
    """State lines (one per second): used by the camera wall, noise in the displayed log."""
    return "state=" in line


# =========================================================================== entry point
def main(argv=None) -> int:
    from .ui import App

    App().run()
    return 0
