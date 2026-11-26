#!/usr/bin/env python3
"""Keeps the vision's sources ACTIVE and checks that they deliver an image.

In OBS, a game capture only grabs frames while the source is used by the scene on air. When the
director cuts to POV_B, POV_A's "Game A" capture stops and the vision goes blind. The fix: add the
vision sources at the VERY BOTTOM of every managed scene (hidden behind everything else, so viewers
never see them, but still active).

    python -m lolauto vision-sources

If the image stays black, the game capture is switched to "any fullscreen application" mode, which
works in more cases than "capture specific window".
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
    print("Scene on air:", cl.get_current_program_scene().current_program_scene_name)
    by_source = {p.vision_source: p for p in cfg.players if p.vision_source}
    sources = list(by_source)
    inputs = {i["inputName"]: i for i in cl.get_input_list().inputs}

    for src in sources:
        if src not in inputs:
            print(f"! source {src!r} not found in OBS")
            continue
        st = cl.get_input_settings(src)
        print(f"{src!r}: type {st.input_kind}, settings {st.input_settings}")
        for scene in cfg.scenes.values():
            names = [it["sourceName"] for it in cl.get_scene_item_list(scene).scene_items]
            if src in names:
                continue
            item = cl.create_scene_item(scene, src, True).scene_item_id
            cl.set_scene_item_index(scene, item, 0)          # very bottom = hidden behind everything else
            print(f"+ {src!r} added to the background of {scene}")

    time.sleep(3)
    for src in sources:
        if src not in inputs:
            continue
        p = by_source[src]
        b = brightness(cl, src, p.width, p.height)
        print(f"{src!r}: mean brightness {b:.1f}")
        kind = cl.get_input_settings(src).input_kind
        if b < 3 and kind.startswith("game_capture"):
            print(f"  black image → switching {src!r} to fullscreen mode")
            cl.set_input_settings(src, {"capture_mode": "any_fullscreen"}, True)
            time.sleep(4)
            b = brightness(cl, src, p.width, p.height)
            print(f"  brightness after the change: {b:.1f}")
        print(f"=> {src!r}: {'OK, image received' if b >= 3 else 'STILL BLACK'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
