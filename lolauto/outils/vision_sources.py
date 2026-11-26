#!/usr/bin/env python3
"""Garde les sources de la vision ACTIVES et vérifie qu'elles donnent une image.

Dans OBS, une capture de jeu ne prend l'image que si la source est utilisée par la scène affichée.
Quand le directeur passe sur POV_B, la capture « Jeu A » de POV_A s'arrête → la vision devient aveugle.
Solution : ajouter les sources de la vision TOUT EN BAS de chaque scène gérée (cachées derrière le
reste, donc invisibles pour les viewers, mais actives).

    python -m lolauto vision-sources

Si l'image reste noire, on bascule la capture de jeu en mode « plein écran » (n'importe quelle
application en plein écran), qui marche dans plus de cas que le mode « fenêtre précise ».
"""

from __future__ import annotations

import sys
import time

import obsws_python as obs

from lolauto import config as cfgmod
from lolauto.vision import decode_obs_image


def brightness(cl, name, w, h) -> float:
    img = decode_obs_image(cl.get_source_screenshot(name, "jpg", w, h, 70).image_data)
    return float(img.mean()) if img is not None else 0.0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = cfgmod.load()
    cl = obs.ReqClient(host=cfg.obs.host, port=cfg.obs.port, password=cfg.obs.password, timeout=5)
    print("Scène affichée :", cl.get_current_program_scene().current_program_scene_name)
    by_source = {p.vision_source: p for p in cfg.players if p.vision_source}
    sources = list(by_source)
    inputs = {i["inputName"]: i for i in cl.get_input_list().inputs}

    for src in sources:
        if src not in inputs:
            print(f"! source {src!r} introuvable dans OBS")
            continue
        st = cl.get_input_settings(src)
        print(f"{src!r} : type {st.input_kind}, réglages {st.input_settings}")
        for scene in cfg.scenes.values():
            names = [it["sourceName"] for it in cl.get_scene_item_list(scene).scene_items]
            if src in names:
                continue
            item = cl.create_scene_item(scene, src, True).scene_item_id
            cl.set_scene_item_index(scene, item, 0)          # tout en bas = caché derrière le reste
            print(f"+ {src!r} ajoutée en arrière-plan de {scene}")

    time.sleep(3)
    for src in sources:
        if src not in inputs:
            continue
        p = by_source[src]
        b = brightness(cl, src, p.width, p.height)
        print(f"{src!r} : luminosité moyenne {b:.1f}")
        kind = cl.get_input_settings(src).input_kind
        if b < 3 and kind.startswith("game_capture"):
            print(f"  image noire → passage de {src!r} en mode « plein écran »")
            cl.set_input_settings(src, {"capture_mode": "any_fullscreen"}, True)
            time.sleep(4)
            b = brightness(cl, src, p.width, p.height)
            print(f"  luminosité après changement : {b:.1f}")
        print(f"=> {src!r} : {'OK, image reçue' if b >= 3 else 'TOUJOURS NOIR'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
