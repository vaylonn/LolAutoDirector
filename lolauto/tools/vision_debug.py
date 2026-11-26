#!/usr/bin/env python3
"""Minimap vision calibration.

    python -m lolauto vision-debug                    # 10 captures of config.toml's sources, 1 per second
    python -m lolauto vision-debug --count 30 --every 0.5
    python -m lolauto vision-debug --image capture.png   # analyse an image already saved
    python -m lolauto vision-debug --champions Ahri,Zed,Jinx,Leona,Darius   # force the enemy champions

Enemy champions are read automatically from the game API (live game OR replay): their official
portraits are downloaded to data/portraits/ and recognised on the minimap.

Writes to logs/vision/: the full image, the cropped minimap and the annotated minimap
(green = camera + "near" radius, red = detected enemies, blue = allies), plus vision.txt.
If the cropped area is not exactly the minimap, adjust [vision].minimap in config.toml.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

from lolauto import config as cfgmod
from lolauto.portraits import enemy_ids
from lolauto.riot_api import RiotClient
from lolauto.scoring import same
from lolauto.vision import VisionRunner, annotate, crop_minimap, decode_obs_image

from lolauto.paths import BASE

OUT = BASE / "logs" / "vision"


def game_enemies(cfg, me_arg):
    """Enemy champions from the local API (also works in replays, without an active player)."""
    try:
        api = RiotClient(cfg.riot.api_url, 2.0)
        data = api.all_game_data()
    except Exception:
        return []
    players = data.get("allPlayers") or []
    me = me_arg
    if not me:
        try:
            me = api.active_name()
        except Exception:
            me = None
    if me:
        return enemy_ids(players, me, same)
    # replay without a player name: take the red team (CHAOS) as the enemy
    from lolauto.portraits import champion_id
    return [champion_id(p) for p in players if p.get("team") == "CHAOS"]


def analyse(runner, pid, frame, cfg, tag, report):
    p = runner.players.get(pid)
    mini = crop_minimap(frame, p.minimap if p is not None and p.minimap else cfg.vision.minimap)
    det = runner.process(pid, frame, time.monotonic())
    cv2.imwrite(str(OUT / f"{tag}_full.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if det is None:
        line = f"{tag}: BLACK image (game not in focus?), ignored"
        print(line, flush=True)
        report.append(line)
        return
    cv2.imwrite(str(OUT / f"{tag}_mini.png"), mini)
    big = cv2.resize(annotate(mini, det, cfg.vision), None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT / f"{tag}_annot.png"), big)
    cam = f"({det.camera[0]:.2f},{det.camera[1]:.2f})" if det.camera else "NOT FOUND"
    named = ", ".join(n for n in det.names if n) or "-"
    line = (f"{tag}: image {frame.shape[1]}x{frame.shape[0]}, minimap {mini.shape[1]}x{mini.shape[0]} | "
            f"camera {cam} | enemies {len(det.enemies)} (near {det.enemies_near(cfg.vision.near_radius)}, "
            f"smoothed {runner.near_count(pid, det.t)}{', approaching' if runner.approaching(pid, det.t) else ''}) "
            f"| recognised: {named} | allies {len(det.allies)}")
    print(line, flush=True)
    report.append(line)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--image", help="analyse an image file instead of OBS")
    ap.add_argument("--source", help="name of an OBS source (otherwise the [[players]] ones)")
    ap.add_argument("--width", type=int, default=1920, help="with --source: game resolution")
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--champions", help="enemy champions, comma-separated (otherwise from the game API)")
    ap.add_argument("--me", help="your player name (replays: tells which team is the enemy)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = cfgmod.load()
    OUT.mkdir(parents=True, exist_ok=True)
    report: list[str] = []
    players = [p for p in cfg.players if p.vision_source]
    if args.source:
        from lolauto.config import PlayerConfig
        players = [PlayerConfig("A", vision_source=args.source, width=args.width, height=args.height)]
    runner = VisionRunner(cfg.vision, players=players)
    champs = ([c.strip() for c in args.champions.split(",") if c.strip()] if args.champions
              else list(cfg.vision.enemy_champions) or game_enemies(cfg, args.me))
    if champs:
        runner.set_enemies(champs, blocking=True)
        print(f"Enemy champions: {', '.join(champs)} | portraits loaded: {len(runner.bank)}", flush=True)
        report.append(f"champions: {champs}, portraits {len(runner.bank)}")
    else:
        print("Enemy champions unknown: red-ring detection only", flush=True)

    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print("Cannot read image:", args.image)
            return 1
        analyse(runner, "X", frame, cfg, Path(args.image).stem, report)
    else:
        import obsws_python as obs

        cl = obs.ReqClient(host=cfg.obs.host, port=cfg.obs.port, password=cfg.obs.password, timeout=5)
        if not runner.players:
            print("No source: set vision_source in config.toml's [[players]]")
            return 1
        for i in range(args.count):
            for pid, p in runner.players.items():
                name = p.vision_source
                try:
                    r = cl.get_source_screenshot(name, "jpg", p.width, p.height, 90)
                except Exception as e:
                    print(f"{pid}: cannot read source {name!r} ({e})")
                    continue
                analyse(runner, pid, decode_obs_image(r.image_data), cfg, f"{pid}_{i:02d}", report)
            time.sleep(args.every)
    if runner.bank.learned:
        report.append(f"portraits relearnt from the minimap: {sorted(runner.bank.learned)}")
        print("Portraits relearnt from the minimap:", ", ".join(sorted(runner.bank.learned)))
    (OUT / "vision.txt").write_text("\n".join(report), encoding="utf-8")
    print(f"\nImages in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
