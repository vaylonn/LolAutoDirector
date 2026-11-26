#!/usr/bin/env python3
"""Calibration de la vision minimap.

    python -m lolauto vision-debug                    # 10 captures des sources de config.toml, 1 par seconde
    python -m lolauto vision-debug --count 30 --every 0.5
    python -m lolauto vision-debug --image capture.png   # analyse une image déjà enregistrée
    python -m lolauto vision-debug --champions Ahri,Zed,Jinx,Leona,Darius   # forcer les champions adverses

Les champions adverses sont lus automatiquement dans l'API du jeu (partie OU rediff) : leurs
portraits officiels sont téléchargés dans data/portraits/ et reconnus sur la minimap.

Écrit dans logs/vision/ : l'image complète, la minimap découpée, et la minimap annotée
(vert = caméra + rayon « proche », rouge = ennemis détectés, bleu = alliés), plus vision.txt.
Si la zone découpée n'est pas exactement la minimap, ajuste [vision].minimap dans config.toml.
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
    """Champions adverses depuis l'API locale (marche aussi en rediff, sans joueur actif)."""
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
    # rediff sans pseudo : on prend l'équipe rouge (CHAOS) comme adversaire
    from lolauto.portraits import champion_id
    return [champion_id(p) for p in players if p.get("team") == "CHAOS"]


def analyse(runner, pid, frame, cfg, tag, report):
    p = runner.players.get(pid)
    mini = crop_minimap(frame, p.minimap if p is not None and p.minimap else cfg.vision.minimap)
    det = runner.process(pid, frame, time.monotonic())
    cv2.imwrite(str(OUT / f"{tag}_full.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if det is None:
        line = f"{tag}: image NOIRE (jeu pas au premier plan ?) — ignorée"
        print(line, flush=True)
        report.append(line)
        return
    cv2.imwrite(str(OUT / f"{tag}_mini.png"), mini)
    big = cv2.resize(annotate(mini, det, cfg.vision), None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(OUT / f"{tag}_annot.png"), big)
    cam = f"({det.camera[0]:.2f},{det.camera[1]:.2f})" if det.camera else "NON TROUVÉE"
    named = ", ".join(n for n in det.names if n) or "-"
    line = (f"{tag}: image {frame.shape[1]}x{frame.shape[0]}, minimap {mini.shape[1]}x{mini.shape[0]} | "
            f"caméra {cam} | ennemis {len(det.enemies)} (proches {det.enemies_near(cfg.vision.near_radius)}, "
            f"lissé {runner.near_count(pid, det.t)}{', approche' if runner.approaching(pid, det.t) else ''}) "
            f"| reconnus : {named} | alliés {len(det.allies)}")
    print(line, flush=True)
    report.append(line)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--every", type=float, default=1.0)
    ap.add_argument("--image", help="analyser un fichier image au lieu d'OBS")
    ap.add_argument("--source", help="nom d'une source OBS (sinon celles des [[players]])")
    ap.add_argument("--width", type=int, default=1920, help="avec --source : résolution du jeu")
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--champions", help="champions adverses, séparés par des virgules (sinon API du jeu)")
    ap.add_argument("--me", help="ton pseudo (rediff : pour savoir quelle équipe est adverse)")
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
        print(f"Champions adverses : {', '.join(champs)} | portraits chargés : {len(runner.bank)}", flush=True)
        report.append(f"champions : {champs}, portraits {len(runner.bank)}")
    else:
        print("Champions adverses inconnus : détection par anneau rouge seulement", flush=True)

    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            print("Image illisible :", args.image)
            return 1
        analyse(runner, "X", frame, cfg, Path(args.image).stem, report)
    else:
        import obsws_python as obs

        cl = obs.ReqClient(host=cfg.obs.host, port=cfg.obs.port, password=cfg.obs.password, timeout=5)
        if not runner.players:
            print("Aucune source : renseigne vision_source dans les [[players]] de config.toml")
            return 1
        for i in range(args.count):
            for pid, p in runner.players.items():
                name = p.vision_source
                try:
                    r = cl.get_source_screenshot(name, "jpg", p.width, p.height, 90)
                except Exception as e:
                    print(f"{pid}: source {name!r} illisible ({e})")
                    continue
                analyse(runner, pid, decode_obs_image(r.image_data), cfg, f"{pid}_{i:02d}", report)
            time.sleep(args.every)
    if runner.bank.learned:
        report.append(f"portraits réappris sur la minimap : {sorted(runner.bank.learned)}")
        print("Portraits réappris sur la minimap :", ", ".join(sorted(runner.bank.learned)))
    (OUT / "vision.txt").write_text("\n".join(report), encoding="utf-8")
    print(f"\nImages dans {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
