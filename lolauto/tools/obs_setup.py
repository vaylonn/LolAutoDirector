#!/usr/bin/env python3
"""Prepares OBS for 2 to 5 players (uses host/port/password and [[players]] from config.toml).

    python -m lolauto obs-setup                        # missing POV_x scenes (coloured test background)
    python -m lolauto obs-setup --split                # dynamic SPLIT scene (any two players)
    python -m lolauto obs-setup --game-capture A       # LoL game capture of player A (this PC) in POV_A

Never changes a scene or source that already exists: replace the test backgrounds with your real
sources afterwards (game capture for yourself, an NDI source for each remote PC).

Dynamic SPLIT: the SPLIT scene holds every POV_x scene twice (left half, right half).
The director only turns on the two players to show: one scene for every pair.
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
            print(f"- {p.scene} already exists, unchanged")
            continue
        cl.create_scene(p.scene)
        cl.create_input(p.scene, f"{p.scene} background", color_kind,
                        {"color": COLORS.get(p.id, 0xFF808080), "width": w, "height": h}, True)
        if text_kind:
            label = f"POV {p.id}" + (f" ({p.name})" if p.name else "")
            cl.create_input(p.scene, f"{p.scene} text", text_kind,
                            {"text": label, "font": {"face": "Arial", "size": 160}}, True)
        print(f"+ {p.scene} created")


def create_split(cl, cfg) -> None:
    split = cfg.obs.scene_split
    if not split:
        print("- [obs] scene_split is empty: no split screen")
        return
    video = cl.get_video_settings()
    w, h = float(video.base_width), float(video.base_height)
    existing = {s["sceneName"] for s in cl.get_scene_list().scenes}
    if split not in existing:
        cl.create_scene(split)
        print(f"+ {split} created")
    items = cl.get_scene_item_list(split).scene_items
    nested = {}
    for it in items:
        if it.get("sourceType") == "OBS_SOURCE_TYPE_SCENE":
            tr = it.get("sceneItemTransform") or {}
            side = "L" if float(tr.get("positionX", 0)) + float(tr.get("width", 0)) / 2 < w / 2 else "R"
            nested[(it["sourceName"], side)] = it["sceneItemId"]
    if items and not nested:
        print(f"- {split} already holds hand-made sources: left as is (fixed SPLIT)")
        return
    for p in cfg.players:
        for side, x in (("L", 0.0), ("R", w / 2)):
            if (p.scene, side) in nested:
                continue
            item = cl.create_scene_item(split, p.scene, False).scene_item_id
            cl.set_scene_item_transform(split, item, {"scaleX": 0.5, "scaleY": 0.5,
                                                      "positionX": x, "positionY": h / 4})
            print(f"+ {p.scene} added to {split} ({'left' if side == 'L' else 'right'} half)")
    print(f"  {split}: the director will show the right two players automatically")


def add_game_capture(cl, cfg, pid: str) -> None:
    p = cfg.player(pid)
    if p is None:
        print(f"! player {pid} is not in config.toml")
        return
    name = p.vision_source or f"Game {pid}"
    inputs = {i["inputName"] for i in cl.get_input_list().inputs}
    if name in inputs:
        print(f"- source {name!r} already exists, unchanged")
        return
    kinds = cl.get_input_kind_list(False).input_kinds
    kind = next((k for k in kinds if k.startswith("game_capture")), None)
    if kind is None:
        print("! \"Game Capture\" is not available (OBS on a Mac?): add the capture by hand")
        return
    cl.create_input(p.scene, name, kind, {"capture_mode": "window", "window": LOL_WINDOW,
                                          "capture_cursor": False}, True)
    print(f"+ source {name!r} (LoL window capture) added to {p.scene}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", action="store_true", help="create or complete the dynamic SPLIT scene")
    ap.add_argument("--game-capture", metavar="ID", help="add player ID's LoL game capture (this PC)")
    ap.add_argument("--config", help="path to config.toml")
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
