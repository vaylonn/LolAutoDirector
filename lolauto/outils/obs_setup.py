#!/usr/bin/env python3
"""Prépare OBS pour 2 à 5 joueurs (utilise host/port/mot de passe et [[players]] de config.toml).

    python -m lolauto obs-setup                        # scènes POV_x manquantes (fond coloré de test)
    python -m lolauto obs-setup --split                # scène SPLIT dynamique (deux joueurs au choix)
    python -m lolauto obs-setup --game-capture A       # capture de jeu LoL du joueur A (ce PC) dans POV_A

Ne modifie jamais une scène ou une source qui existe déjà : remplace ensuite les fonds de test par
tes vraies sources (capture de jeu pour toi, source NDI pour chaque PC distant).

SPLIT dynamique : la scène SPLIT contient chaque scène POV_x deux fois (moitié gauche, moitié droite).
Le directeur n'allume que les deux joueurs à montrer — une seule scène pour toutes les paires.
"""

from __future__ import annotations

import argparse
import sys

import obsws_python as obs

from lolauto import config as cfgmod

COLORS = {"A": 0xFF3030D0, "B": 0xFFD06030, "C": 0xFF30A030, "D": 0xFF30A0D0, "E": 0xFFA030A0}  # ABGR
LOL_WINDOW = "League of Legends (TM) Client:RiotWindowClass:League of Legends.exe"


def connect(cfg):
    return obs.ReqClient(host=cfg.obs.host, port=cfg.obs.port, password=cfg.obs.password, timeout=5)


def create_player_scenes(cl, cfg) -> None:
    kinds = cl.get_input_kind_list(False).input_kinds
    color_kind = next((k for k in kinds if k.startswith("color_source")), "color_source")
    text_kind = next((k for k in kinds if k.startswith("text_gdiplus") or k.startswith("text_ft2")), None)
    video = cl.get_video_settings()
    w, h = video.base_width, video.base_height
    existing = {s["sceneName"] for s in cl.get_scene_list().scenes}
    for p in cfg.players:
        if p.scene in existing:
            print(f"- {p.scene} existe déjà, inchangée")
            continue
        cl.create_scene(p.scene)
        cl.create_input(p.scene, f"{p.scene} fond", color_kind,
                        {"color": COLORS.get(p.id, 0xFF808080), "width": w, "height": h}, True)
        if text_kind:
            label = f"POV {p.id}" + (f" ({p.name})" if p.name else "")
            cl.create_input(p.scene, f"{p.scene} texte", text_kind,
                            {"text": label, "font": {"face": "Arial", "size": 160}}, True)
        print(f"+ {p.scene} créée")


def create_split(cl, cfg) -> None:
    split = cfg.obs.scene_split
    if not split:
        print("- [obs] scene_split est vide : pas d'écran partagé")
        return
    video = cl.get_video_settings()
    w, h = float(video.base_width), float(video.base_height)
    existing = {s["sceneName"] for s in cl.get_scene_list().scenes}
    if split not in existing:
        cl.create_scene(split)
        print(f"+ {split} créée")
    items = cl.get_scene_item_list(split).scene_items
    nested = {}
    for it in items:
        if it.get("sourceType") == "OBS_SOURCE_TYPE_SCENE":
            tr = it.get("sceneItemTransform") or {}
            side = "G" if float(tr.get("positionX", 0)) + float(tr.get("width", 0)) / 2 < w / 2 else "D"
            nested[(it["sourceName"], side)] = it["sceneItemId"]
    if items and not nested:
        print(f"- {split} contient déjà des sources « à la main » : laissée telle quelle (SPLIT fixe)")
        return
    for p in cfg.players:
        for side, x in (("G", 0.0), ("D", w / 2)):
            if (p.scene, side) in nested:
                continue
            item = cl.create_scene_item(split, p.scene, False).scene_item_id
            cl.set_scene_item_transform(split, item, {"scaleX": 0.5, "scaleY": 0.5,
                                                      "positionX": x, "positionY": h / 4})
            print(f"+ {p.scene} ajoutée dans {split} (moitié {'gauche' if side == 'G' else 'droite'})")
    print(f"  {split} : le directeur affichera automatiquement les deux bons joueurs")


def add_game_capture(cl, cfg, pid: str) -> None:
    p = cfg.player(pid)
    if p is None:
        print(f"! joueur {pid} absent de config.toml")
        return
    name = p.vision_source or f"Jeu {pid}"
    inputs = {i["inputName"] for i in cl.get_input_list().inputs}
    if name in inputs:
        print(f"- source {name!r} existe déjà, inchangée")
        return
    kinds = cl.get_input_kind_list(False).input_kinds
    kind = next((k for k in kinds if k.startswith("game_capture")), None)
    if kind is None:
        print("! « Capture de jeu » indisponible (OBS sur Mac ?) : ajoute la capture à la main")
        return
    cl.create_input(p.scene, name, kind, {"capture_mode": "window", "window": LOL_WINDOW,
                                          "capture_cursor": False}, True)
    print(f"+ source {name!r} (capture de la fenêtre LoL) ajoutée dans {p.scene}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", action="store_true", help="crée / complète la scène SPLIT dynamique")
    ap.add_argument("--game-capture", metavar="ID", help="ajoute la capture de jeu LoL du joueur ID (ce PC)")
    ap.add_argument("--config", help="chemin de config.toml")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = cfgmod.load(args.config)
    cl = connect(cfg)
    create_player_scenes(cl, cfg)
    if args.split:
        create_split(cl, cfg)
    if args.game_capture:
        add_game_capture(cl, cfg, args.game_capture)
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
