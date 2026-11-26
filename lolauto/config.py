"""Chargement et validation de config.toml (les clés inconnues sont refusées : pas de faute de frappe silencieuse)."""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.9 / 3.10 (ex. Python fourni par Xcode sur macOS)
    import tomli as tomllib

from .scoring import ScoringConfig
from .switcher import SwitchConfig
from .vision import VisionConfig

from .paths import BASE

DEFAULT_PATH = BASE / "config.toml"


@dataclass
class NetworkConfig:
    director_host: str = "127.0.0.1"
    director_port: int = 5005
    token: str = ""


@dataclass
class RiotConfig:
    api_url: str = "https://127.0.0.1:2999"
    poll_interval: float = 0.3
    timeout: float = 1.0


@dataclass
class ObsConfig:
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""
    scene_split: str = ""


PLAYER_IDS = ("A", "B", "C", "D", "E")


@dataclass
class PlayerConfig:
    """Un joueur = un PC de jeu (2 à 5). Le PC qui diffuse est en général « A »."""
    id: str = "A"
    name: str = ""                 # pour les journaux et le lanceur
    scene: str = ""                # scène OBS de son POV ("" = "POV_<id>")
    vision_source: str = ""        # source OBS lue par la vision (capture de jeu / NDI) ; "" = pas de vision
    width: int = 1920              # résolution du jeu de CE joueur (taille demandée à OBS pour la vision)
    height: int = 1080
    minimap: list = field(default_factory=list)   # zone de minimap propre à ce joueur ([] = [vision].minimap)

    def __post_init__(self):
        if self.id not in PLAYER_IDS:
            raise ValueError(f"[[players]] id doit être l'une de {PLAYER_IDS}, pas {self.id!r}")
        if not self.scene:
            self.scene = f"POV_{self.id}"
        if self.width < 320 or self.height < 240:
            raise ValueError(f"[[players]] {self.id} : résolution {self.width}x{self.height} invalide")


@dataclass
class Config:
    network: NetworkConfig = field(default_factory=NetworkConfig)
    riot: RiotConfig = field(default_factory=RiotConfig)
    obs: ObsConfig = field(default_factory=ObsConfig)
    switching: SwitchConfig = field(default_factory=SwitchConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    players: list = field(default_factory=lambda: [PlayerConfig("A"), PlayerConfig("B")])

    @property
    def scenes(self) -> dict[str, str]:
        scenes = {p.id: p.scene for p in self.players}
        if self.obs.scene_split:
            scenes["SPLIT"] = self.obs.scene_split
        return scenes

    @property
    def player_ids(self) -> list[str]:
        return [p.id for p in self.players]

    def player(self, pid: str) -> PlayerConfig | None:
        return next((p for p in self.players if p.id == pid), None)


def _build(cls, data: dict, where: str):
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data) - names
    if unknown:
        raise ValueError(
            f"config.toml [{where}] : clé(s) inconnue(s) {sorted(unknown)} — attendu : {sorted(names)}")
    return cls(**data)


def load(path: str | Path | None = None) -> Config:
    p = Path(path) if path else DEFAULT_PATH
    with open(p, "rb") as f:
        raw = tomllib.load(f)
    return from_dict(raw)


def loads(text: str) -> Config:
    return from_dict(tomllib.loads(text))


SECTIONS = {"network": NetworkConfig, "riot": RiotConfig, "obs": ObsConfig,
            "switching": SwitchConfig, "scoring": ScoringConfig, "vision": VisionConfig}


def from_dict(raw: dict) -> Config:
    unknown = set(raw) - set(SECTIONS) - {"players"}
    if unknown:
        raise ValueError(f"config.toml : section(s) inconnue(s) {sorted(unknown)}")
    cfg = Config(**{name: _build(cls, raw.get(name, {}), name) for name, cls in SECTIONS.items()})
    if "players" in raw:
        players = [_build(PlayerConfig, p, "players") for p in raw["players"]]
        ids = [p.id for p in players]
        if not 1 <= len(players) <= 5:
            raise ValueError("config.toml : il faut entre 1 et 5 [[players]]")
        if len(set(ids)) != len(ids):
            raise ValueError(f"config.toml : identifiants de joueurs en double {ids}")
        cfg.players = players
    return cfg
