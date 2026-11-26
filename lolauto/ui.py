"""The app's interface (customtkinter), designed like a TV control room.

Each player is a "camera" with a tally light, as on a broadcast switcher:
red = on air, green = in game, grey = offline. The Control room page is a wall of these
cameras, updated live from the director's logs.
"""

from __future__ import annotations

import os
import queue
import secrets
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import customtkinter as ctk

from . import config_writer
from .config import PLAYER_IDS, Config
from .launcher import (CONFIG_FILE, RESOLUTIONS, LocalSettings, ProcessGroup, build_commands,
                       check_local, config_values, is_noise, load_config, local_ip, parse_line, values_to_config)
from .paths import BASE, cli

# --------------------------------------------------------------------------- design tokens
C = {
    "bg": "#141922",          # control room: blue-grey slate, not pure black
    "side": "#10141C",
    "panel": "#1B2130",
    "raised": "#232B3C",
    "line": "#2D364A",
    "text": "#E7EBF3",
    "muted": "#8E98AD",
    "faint": "#5C667B",
    "program": "#E5484D",     # red tally: on air
    "preview": "#3DD68C",     # green tally: in game, ready
    "idle": "#465066",        # tally off: offline
    "action": "#7AA2F7",      # buttons and selection
    "action_hover": "#94B5FA",
    "action_deep": "#3B57B0",  # segmented button selection (light text stays readable)
    "action_deep_hover": "#4A67C4",
    "on_action": "#0F1420",
}
SCORE_MAX = 10.0              # a score of 10 fills the bar


def _family(*candidates: str) -> str:
    try:
        available = set(tkfont.families())
    except tk.TclError:
        return "TkDefaultFont"
    return next((f for f in candidates if f in available), "TkDefaultFont")


class Fonts:
    def __init__(self):
        display = _family("Segoe UI Variable Display", "Segoe UI", "SF Pro Display", "Inter", "Helvetica Neue",
                          "DejaVu Sans")
        text = _family("Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Inter", "Helvetica Neue", "DejaVu Sans")
        mono = _family("Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono")
        self.title = ctk.CTkFont(display, 26, "bold")
        self.h2 = ctk.CTkFont(display, 17, "bold")
        self.letter = ctk.CTkFont(display, 44, "bold")
        self.body = ctk.CTkFont(text, 13)
        self.strong = ctk.CTkFont(text, 13, "bold")
        self.small = ctk.CTkFont(text, 11)
        self.nav = ctk.CTkFont(text, 14)
        self.mono = ctk.CTkFont(mono, 11)


def _open_folder(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# --------------------------------------------------------------------------- camera (wall tile)
class CameraTile(ctk.CTkFrame):
    def __init__(self, master, app: "App", pid: str):
        super().__init__(master, fg_color=C["panel"], corner_radius=14, border_width=2, border_color=C["line"])
        self.app, self.pid = app, pid
        self.tally = ctk.CTkFrame(self, height=6, corner_radius=3, fg_color=C["idle"])
        self.tally.pack(fill="x", padx=14, pady=(12, 0))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(8, 0))
        self.letter = ctk.CTkLabel(row, text=pid, font=app.f.letter, text_color=C["text"], width=40, anchor="w")
        self.letter.pack(side="left")
        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.name = ctk.CTkLabel(info, text="", font=app.f.strong, text_color=C["text"], anchor="w")
        self.name.pack(fill="x")
        self.status = ctk.CTkLabel(info, text="Offline", font=app.f.small, text_color=C["muted"], anchor="w")
        self.status.pack(fill="x")
        self.bar = ctk.CTkProgressBar(self, height=8, corner_radius=4, fg_color=C["raised"],
                                      progress_color=C["action"])
        self.bar.set(0)
        self.bar.pack(fill="x", padx=16, pady=(12, 4))
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=16, pady=(0, 12))
        self.scene = ctk.CTkLabel(foot, text="", font=app.f.small, text_color=C["faint"], anchor="w")
        self.scene.pack(side="left")
        self.score = ctk.CTkLabel(foot, text="—", font=app.f.small, text_color=C["muted"], anchor="e")
        self.score.pack(side="right")

    def render(self, name: str, scene: str, on_air: bool, in_game: bool, score: float | None, running: bool):
        self.name.configure(text=name or f"Player {self.pid}")
        self.scene.configure(text=scene)
        if on_air:
            color, text, border = C["program"], "On air", C["program"]
        elif score is not None and score < 0:
            color, text, border = C["idle"], "Dead, waiting to respawn", C["line"]
        elif in_game or score is not None:
            color, text, border = C["preview"], "In game", C["line"]
        elif running:
            color, text, border = C["idle"], "Waiting for a game", C["line"]
        else:
            color, text, border = C["idle"], "Offline", C["line"]
        self.tally.configure(fg_color=color)
        self.configure(border_color=border)
        self.status.configure(text=text, text_color=C["program"] if on_air else C["muted"])
        if score is None or score < 0:
            self.bar.set(0)
            self.score.configure(text="—" if score is None else "dead")
        else:
            self.bar.set(max(0.0, min(1.0, score / SCORE_MAX)))
            self.bar.configure(progress_color=C["program"] if on_air else C["action"])
            self.score.configure(text=f"interest {score:.1f}")


# --------------------------------------------------------------------------- application
class App:
    POLL_MS = 120

    def __init__(self, cfg: Config | None = None, local: LocalSettings | None = None):
        ctk.set_appearance_mode("dark")
        self.cfg = cfg or load_config()
        self.local = local or LocalSettings.load()
        self.group = ProcessGroup()
        self.tools: dict[str, ProcessGroup] = {}     # simulation, calibration… (one run each)
        self.root = ctk.CTk(fg_color=C["bg"])
        self.root.title("LoL Auto Director")
        self.root.geometry("1180x760")
        self.root.minsize(1040, 680)
        self.f = Fonts()
        self.vars: dict[str, tk.Variable] = {}
        self.live = {"on_air": [], "scores": {}, "in_game": set(), "history": [], "obs": None}
        self._dirty = False
        self._build()
        self._fill(config_values(self.cfg, self.local))
        self.show("control")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(self.POLL_MS, self._poll)

    # ------------------------------------------------------------------ small builders
    def var(self, key: str, kind=str):
        if key not in self.vars:
            v = tk.BooleanVar(self.root) if kind is bool else tk.StringVar(self.root)
            v.trace_add("write", lambda *_: self._mark_dirty())
            self.vars[key] = v
        return self.vars[key]

    def label(self, master, text, font=None, color=None, **kw):
        return ctk.CTkLabel(master, text=text, font=font or self.f.body, text_color=color or C["text"],
                            anchor="w", justify="left", **kw)

    def entry(self, master, key, width=200, **kw):
        return ctk.CTkEntry(master, textvariable=self.var(key), width=width, font=self.f.body,
                            fg_color=C["raised"], border_color=C["line"], text_color=C["text"], **kw)

    def button(self, master, text, command, primary=False, width=None, danger=False):
        if primary:
            fg, hover, tc = (C["program"], "#EE6A6E", "#FFFFFF") if danger else (C["action"], C["action_hover"],
                                                                                  C["on_action"])
            return ctk.CTkButton(master, text=text, command=command, font=self.f.strong, fg_color=fg,
                                 hover_color=hover, text_color=tc, corner_radius=10, height=40,
                                 width=width or 180)
        return ctk.CTkButton(master, text=text, command=command, font=self.f.body, fg_color=C["raised"],
                             hover_color=C["line"], text_color=C["text"], corner_radius=10, height=34,
                             width=width or 140, border_width=1, border_color=C["line"])

    def switch(self, master, text, key):
        return ctk.CTkSwitch(master, text=text, variable=self.var(key, bool), font=self.f.body,
                             text_color=C["text"], progress_color=C["action"], button_color=C["text"],
                             fg_color=C["idle"])

    def section(self, master, title, hint=None):
        box = ctk.CTkFrame(master, fg_color=C["panel"], corner_radius=12)
        box.pack(fill="x", pady=(0, 14))
        self.label(box, title, self.f.h2).pack(fill="x", padx=20, pady=(16, 2 if hint else 10))
        if hint:
            self.label(box, hint, self.f.small, C["muted"], wraplength=760).pack(fill="x", padx=20, pady=(0, 10))
        body = ctk.CTkFrame(box, fg_color="transparent")
        body.pack(fill="x", padx=20, pady=(0, 16))
        return body

    def field(self, body, row, label, widget, hint=None):
        self.label(body, label, self.f.body, C["muted"]).grid(row=row, column=0, sticky="w", pady=6, padx=(0, 18))
        widget.grid(row=row, column=1, sticky="w", pady=6)
        if hint:
            self.label(body, hint, self.f.small, C["faint"]).grid(row=row, column=2, sticky="w", padx=14)

    def slider(self, body, row, label, key, lo, hi, step, unit=""):
        holder = ctk.CTkFrame(body, fg_color="transparent")
        value = self.label(holder, "", self.f.strong, width=70)
        sl = ctk.CTkSlider(holder, from_=lo, to=hi, number_of_steps=round((hi - lo) / step), width=320,
                           progress_color=C["action"], button_color=C["text"], button_hover_color=C["action_hover"],
                           fg_color=C["raised"])

        def from_slider(x):
            self.var(key).set(f"{round(x / step) * step:.2f}".rstrip("0").rstrip("."))

        def from_var(*_):
            try:
                x = float(str(self.var(key).get()).replace(",", "."))
            except ValueError:
                return
            sl.set(x)
            value.configure(text=f"{x:g}{unit}")

        sl.configure(command=from_slider)
        self.var(key).trace_add("write", from_var)
        sl.pack(side="left")
        value.pack(side="left", padx=(12, 0))
        self.field(body, row, label, holder)

    # ------------------------------------------------------------------ layout
    def _build(self):
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        side = ctk.CTkFrame(self.root, fg_color=C["side"], corner_radius=0, width=220)
        side.grid(row=0, column=0, sticky="ns")
        side.grid_propagate(False)
        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=22, pady=(26, 30))
        self.brand_tally = ctk.CTkFrame(brand, width=12, height=12, corner_radius=6, fg_color=C["idle"])
        self.brand_tally.pack(side="left", padx=(0, 10))
        self.label(brand, "LoL Auto Director", self.f.strong).pack(side="left")

        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav: dict[str, ctk.CTkButton] = {}
        main = ctk.CTkFrame(self.root, fg_color=C["bg"], corner_radius=0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)
        self.main = main
        for key, title in (("control", "Control room"), ("players", "Players"), ("obs", "OBS"),
                           ("directing", "Directing"), ("vision", "Vision"), ("pc", "This PC")):
            b = ctk.CTkButton(side, text=title, anchor="w", font=self.f.nav, height=40, corner_radius=8,
                              fg_color="transparent", hover_color=C["panel"], text_color=C["muted"],
                              command=lambda k=key: self.show(k))
            b.pack(fill="x", padx=12, pady=2)
            self.nav[key] = b
            page = ctk.CTkScrollableFrame(main, fg_color=C["bg"], corner_radius=0) if key != "control" else \
                ctk.CTkFrame(main, fg_color=C["bg"], corner_radius=0)
            self.pages[key] = page

        foot = ctk.CTkFrame(side, fg_color="transparent")
        foot.pack(side="bottom", fill="x", padx=16, pady=18)
        self.save_state = self.label(foot, "Settings saved", self.f.small, C["faint"])
        self.save_state.pack(fill="x", pady=(0, 8))
        self.btn_save = self.button(foot, "Save", self._save, width=188)
        self.btn_save.pack(fill="x")

        self._page_control(self.pages["control"])
        self._page_players(self.pages["players"])
        self._page_obs(self.pages["obs"])
        self._page_directing(self.pages["directing"])
        self._page_vision(self.pages["vision"])
        self._page_pc(self.pages["pc"])

    def show(self, key: str):
        for k, page in self.pages.items():
            page.grid_forget()
            self.nav[k].configure(fg_color="transparent", text_color=C["muted"])
        self.pages[key].grid(row=0, column=0, sticky="nsew", padx=32, pady=(28, 20))
        self.nav[key].configure(fg_color=C["panel"], text_color=C["text"])

    def _header(self, page, title, subtitle):
        self.label(page, title, self.f.title).pack(fill="x")
        self.label(page, subtitle, self.f.body, C["muted"], wraplength=820).pack(fill="x", pady=(4, 20))

    # ------------------------------------------------------------------ Control room page
    def _page_control(self, page):
        top = ctk.CTkFrame(page, fg_color="transparent")
        top.pack(fill="x")
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        self.label(left, "Control room", self.f.title).pack(fill="x")
        self.control_sub = self.label(left, "", self.f.body, C["muted"])
        self.control_sub.pack(fill="x", pady=(4, 0))
        self.btn_go = self.button(top, "Start directing", self._toggle, primary=True, width=200)
        self.btn_go.pack(side="right")
        self.btn_sim = self.button(top, "Test without playing", self._simulate, width=150)
        self.btn_sim.pack(side="right", padx=10)

        self.grid = ctk.CTkFrame(page, fg_color="transparent")
        self.grid.pack(fill="x", pady=(22, 16))
        for c in range(3):
            self.grid.grid_columnconfigure(c, weight=1, uniform="cam")
        self.tiles = {pid: CameraTile(self.grid, self, pid) for pid in PLAYER_IDS}

        bottom = ctk.CTkFrame(page, fg_color="transparent")
        bottom.pack(fill="both", expand=True)
        bottom.grid_columnconfigure(0, weight=2)
        bottom.grid_columnconfigure(1, weight=3)
        bottom.grid_rowconfigure(0, weight=1)
        hist = ctk.CTkFrame(bottom, fg_color=C["panel"], corner_radius=12)
        hist.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.label(hist, "Recent cuts", self.f.strong).pack(fill="x", padx=18, pady=(14, 6))
        self.hist_box = ctk.CTkFrame(hist, fg_color="transparent")
        self.hist_box.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        logp = ctk.CTkFrame(bottom, fg_color=C["panel"], corner_radius=12)
        logp.grid(row=0, column=1, sticky="nsew")
        head = ctk.CTkFrame(logp, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(14, 6))
        self.label(head, "Log", self.f.strong).pack(side="left")
        ctk.CTkButton(head, text="Open folder", width=120, height=26, font=self.f.small,
                      fg_color="transparent", hover_color=C["raised"], text_color=C["muted"],
                      command=lambda: _open_folder(BASE / "logs")).pack(side="right")
        self.log = ctk.CTkTextbox(logp, font=self.f.mono, fg_color=C["side"], text_color=C["muted"],
                                  corner_radius=8, wrap="none")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.log.configure(state="disabled")

    def _layout_tiles(self):
        for t in self.tiles.values():
            t.grid_forget()
        active = [p.id for p in self.cfg.players]
        for i, pid in enumerate(active):
            self.tiles[pid].grid(row=i // 3, column=i % 3, sticky="nsew", padx=6, pady=6)

    # ------------------------------------------------------------------ Players page
    def _page_players(self, page):
        self._header(page, "Players", "2 to 5 players. The video source is what the vision reads for each player: "
                                      "your game capture for you, each teammate's NDI source for them.")
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 12))
        self.button(bar, "Read OBS sources", self._read_obs, width=190).pack(side="left")
        self.obs_hint = self.label(bar, "", self.f.small, C["muted"])
        self.obs_hint.pack(side="left", padx=14)
        self.source_boxes = []
        for pid in PLAYER_IDS:
            card = ctk.CTkFrame(page, fg_color=C["panel"], corner_radius=12)
            card.pack(fill="x", pady=5)
            self.label(card, pid, self.f.h2, width=36).grid(row=0, column=0, rowspan=2, padx=(20, 8), pady=14)
            sw = ctk.CTkSwitch(card, text="", width=44, variable=self.var(f"p.{pid}.on", bool),
                               progress_color=C["action"], fg_color=C["idle"], button_color=C["text"])
            sw.grid(row=0, column=1, rowspan=2, padx=(0, 12))
            for col, (lab, key, w) in enumerate((("Name", "name", 150), ("OBS scene", "scene", 120)), start=2):
                self.label(card, lab, self.f.small, C["faint"]).grid(row=0, column=col, sticky="w", padx=6,
                                                                    pady=(12, 0))
                self.entry(card, f"p.{pid}.{key}", width=w).grid(row=1, column=col, padx=6, pady=(2, 14))
            self.label(card, "Video source", self.f.small, C["faint"]).grid(row=0, column=4, sticky="w", padx=6,
                                                                           pady=(12, 0))
            cb = ctk.CTkComboBox(card, variable=self.var(f"p.{pid}.vision_source"), values=[""], width=210,
                                 font=self.f.body, fg_color=C["raised"], border_color=C["line"],
                                 button_color=C["line"], dropdown_fg_color=C["raised"])
            cb.grid(row=1, column=4, padx=6, pady=(2, 14))
            self.source_boxes.append(cb)
            self.label(card, "Game resolution", self.f.small, C["faint"]).grid(row=0, column=5, sticky="w",
                                                                                padx=6, pady=(12, 0))
            ctk.CTkComboBox(card, variable=self.var(f"p.{pid}.res"), values=RESOLUTIONS, width=130,
                            font=self.f.body, fg_color=C["raised"], border_color=C["line"], button_color=C["line"],
                            dropdown_fg_color=C["raised"]).grid(row=1, column=5, padx=(6, 20), pady=(2, 14))

    # ------------------------------------------------------------------ OBS page
    def _page_obs(self, page):
        self._header(page, "OBS", "The director drives OBS through its WebSocket server "
                                  "(OBS › Tools › WebSocket Server Settings).")
        b = self.section(page, "Connection")
        self.field(b, 0, "Address", self.entry(b, "obs.host", 200))
        self.field(b, 1, "Port", self.entry(b, "obs.port", 90))
        pw = ctk.CTkFrame(b, fg_color="transparent")
        self.pw_entry = self.entry(pw, "obs.password", 240, show="•")
        self.pw_entry.pack(side="left")
        ctk.CTkButton(pw, text="Show", width=80, height=30, font=self.f.small, fg_color="transparent",
                      hover_color=C["raised"], text_color=C["muted"],
                      command=lambda: self.pw_entry.configure(show="" if self.pw_entry.cget("show") else "•")
                      ).pack(side="left", padx=6)
        self.field(b, 2, "Password", pw)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.button(row, "Test connection", self._read_obs, width=170).pack(side="left")
        self.obs_status = self.label(row, "", self.f.small, C["muted"])
        self.obs_status.pack(side="left", padx=14)

        b = self.section(page, "Scenes", "One POV_x scene per player plus a split-screen scene. \"Prepare scenes\" "
                                         "creates what is missing without touching what exists, and adds your game capture.")
        self.field(b, 0, "Split-screen scene", self.entry(b, "obs.scene_split", 160), "empty = never split the screen")
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.button(row, "Prepare scenes", self._prepare_obs, width=180).pack(side="left")
        self.button(row, "Keep captures active", lambda: self._tool(
            "Vision", cli("vision-sources")), width=220).pack(side="left", padx=10)

    # ------------------------------------------------------------------ Directing page
    def _page_directing(self, page):
        self._header(page, "Directing", "When the camera moves to another player. Longer times give calmer "
                                          "directing; shorter times make it snappier.")
        b = self.section(page, "Camera cuts")
        self.slider(b, 0, "Minimum time on a player", "switching.min_hold", 1, 15, 0.5, " s")
        self.slider(b, 1, "Score lead to cut", "switching.margin", 0.5, 5, 0.25)
        self.slider(b, 2, "Urgent cut after", "switching.fast_min_hold", 0.5, 6, 0.5, " s")
        self.slider(b, 3, "Score lead for an urgent cut", "switching.fast_margin", 2, 10, 0.5)
        self.slider(b, 4, "Pause after a manual switch", "switching.manual_pause", 5, 60, 5, " s")
        b = self.section(page, "Split screen", "The two most interesting players side by side. \"Separate fights\" "
                                                "keeps it for moments when each one fights on their own.")
        self.switch(b, "Allow split screen", "switching.split_enabled").grid(row=0, column=0, columnspan=2,
                                                                                    sticky="w", pady=6)
        seg = ctk.CTkSegmentedButton(b, values=["Separate fights", "Always"], font=self.f.body,
                                     selected_color=C["action_deep"], selected_hover_color=C["action_deep_hover"],
                                     unselected_color=C["raised"], fg_color=C["raised"], text_color=C["text"],
                                     command=lambda v: self.var("switching.split_mode").set(
                                         "separate_fights" if v == "Separate fights" else "always"))
        self.var("switching.split_mode").trace_add("write", lambda *_: seg.set(
            "Separate fights" if self.var("switching.split_mode").get() == "separate_fights" else "Always"))
        self.field(b, 1, "When", seg)
        self.slider(b, 2, "Action level needed", "switching.split_threshold", 1, 8, 0.5)

    # ------------------------------------------------------------------ Vision page
    def _page_vision(self, page):
        self._header(page, "Vision", "The director reads each player's minimap in OBS to see enemies coming "
                                     "before the fight starts.")
        b = self.section(page, "Minimap reading")
        self.switch(b, "Turn on the vision", "vision.enabled").grid(row=0, column=0, columnspan=2, sticky="w", pady=6)
        self.slider(b, 1, "Read every", "vision.interval", 0.1, 1.0, 0.05, " s")
        self.slider(b, 2, "Bonus per extra enemy", "vision.w_enemy_near", 0, 3, 0.1)
        b = self.section(page, "Calibration", "The area is in fractions of the image and fits every 16:9 screen. "
                                              "\"Calibrate\" saves 5 annotated captures during a game.")
        zone = ctk.CTkFrame(b, fg_color="transparent")
        for i, lab in enumerate(("left", "top", "right", "bottom")):
            cell = ctk.CTkFrame(zone, fg_color="transparent")
            cell.pack(side="left", padx=(0, 10))
            self.label(cell, lab, self.f.small, C["faint"]).pack(fill="x")
            self.entry(cell, f"vision.minimap.{i}", 72).pack()
        self.field(b, 0, "Minimap area", zone)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
        self.button(row, "Calibrate", lambda: self._tool(
            "Calibration", cli("vision-debug", "--count", "5", "--every", "1"), open_dir=BASE / "logs" / "vision"),
            width=120).pack(side="left")

    # ------------------------------------------------------------------ This PC page
    def _page_pc(self, page):
        self._header(page, "This PC", "Every PC uses the same config.toml; only this PC's role changes.")
        b = self.section(page, "Role")
        seg = ctk.CTkSegmentedButton(b, values=["Streaming PC", "Player PC"], font=self.f.body,
                                     selected_color=C["action_deep"], selected_hover_color=C["action_deep_hover"],
                                     unselected_color=C["raised"], fg_color=C["raised"], text_color=C["text"],
                                     command=lambda v: self.var("local.role").set(
                                         "streamer" if v == "Streaming PC" else "player"))
        self.var("local.role").trace_add("write", lambda *_: seg.set(
            "Streaming PC" if self.var("local.role").get() == "streamer" else "Player PC"))
        self.field(b, 0, "This PC is the", seg, "Streaming PC: OBS + director + your agent")
        letters = ctk.CTkSegmentedButton(b, values=list(PLAYER_IDS), variable=self.var("local.player_id"),
                                         font=self.f.strong, selected_color=C["action_deep"],
                                         selected_hover_color=C["action_deep_hover"], unselected_color=C["raised"],
                                         fg_color=C["raised"], text_color=C["text"])
        self.field(b, 1, "Player", letters)
        b = self.section(page, "Network", "The same on every PC.")
        ip = ctk.CTkFrame(b, fg_color="transparent")
        self.entry(ip, "network.director_host", 170).pack(side="left")
        ctk.CTkButton(ip, text="Use this PC's IP", width=150, height=30, font=self.f.small,
                      fg_color="transparent", hover_color=C["raised"], text_color=C["action"],
                      command=lambda: self.var("network.director_host").set(local_ip())).pack(side="left", padx=8)
        self.field(b, 0, "Streaming PC IP", ip)
        self.field(b, 1, "UDP port", self.entry(b, "network.director_port", 90))
        tok = ctk.CTkFrame(b, fg_color="transparent")
        self.entry(tok, "network.token", 170).pack(side="left")
        ctk.CTkButton(tok, text="New token", width=110, height=30, font=self.f.small, fg_color="transparent",
                      hover_color=C["raised"], text_color=C["action"],
                      command=lambda: self.var("network.token").set(secrets.token_urlsafe(9))).pack(side="left", padx=8)
        self.field(b, 2, "Token", tok)
        b = self.section(page, "Log")
        self.switch(b, "Log your agent's scores every second", "local.verbose").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=6)
        self.label(b, f"Folder: {BASE}", self.f.small, C["faint"]).grid(row=1, column=0, columnspan=3, sticky="w",
                                                                         pady=(8, 0))

    # ------------------------------------------------------------------ data
    def _fill(self, values: dict):
        for k, v in values.items():
            self.var(k, bool if isinstance(v, bool) else str).set(v)
        self._dirty = False
        self._refresh_save_state()
        self._layout_tiles()
        self._render()

    def values(self) -> dict:
        return {k: v.get() for k, v in self.vars.items()}

    def form_to_config(self):
        return values_to_config(self.cfg, self.values())

    def _mark_dirty(self):
        self._dirty = True
        if hasattr(self, "save_state"):
            self._refresh_save_state()

    def _refresh_save_state(self):
        self.save_state.configure(text="Unsaved changes" if self._dirty else "Settings saved",
                                  text_color=C["action"] if self._dirty else C["faint"])

    def _save(self, quiet=False) -> bool:
        try:
            cfg, local = self.form_to_config()
        except ValueError as e:
            messagebox.showerror("Setting to fix", str(e))
            return False
        config_writer.save(cfg, CONFIG_FILE)
        local.save()
        self.cfg, self.local = cfg, local
        self._dirty = False
        self._refresh_save_state()
        self._layout_tiles()
        if not quiet:
            self._print("Settings saved to config.toml and this_pc.json")
        return True

    # ------------------------------------------------------------------ actions
    def _toggle(self):
        if self.group.running():
            self.group.stop()
            self._print("Directing stopped")
        else:
            if not self._save(quiet=True):
                return
            for w in check_local(self.cfg, self.local):
                self._print("Warning: " + w)
            self.live.update(on_air=[], scores={}, in_game=set(), obs=None)
            for name, argv in build_commands(self.cfg, self.local):
                self.group.start(name, argv)
            self._print("Directing started")
        self._render()

    def _tool(self, name, argv, open_dir=None):
        busy = self.tools.get(name)
        if busy is not None and busy.running():
            self._print(f"{name} is already running")
            return
        if not self._save(quiet=True):
            return
        g = ProcessGroup(lines=self.group.lines)
        self.tools[name] = g
        g.start(name, argv)
        self._print(f"{name} started")
        if open_dir is not None:
            def wait_and_open():
                for _, p in g.procs:
                    p.wait()
                _open_folder(open_dir)
            threading.Thread(target=wait_and_open, daemon=True).start()

    def _simulate(self):
        self.show("control")
        self._tool("Simulation", cli("simulate"))

    def _prepare_obs(self):
        self._tool("Prepare OBS", cli("obs-setup", "--split", "--game-capture", str(self.var("local.player_id").get())))

    def _read_obs(self):
        if not self._save(quiet=True):
            return
        self.obs_status.configure(text="Connecting…", text_color=C["muted"])
        cfg = self.cfg

        def work():
            try:
                import obsws_python as obs

                cl = obs.ReqClient(host=cfg.obs.host, port=cfg.obs.port, password=cfg.obs.password, timeout=3)
                version = cl.get_version().obs_version
                scenes = [s["sceneName"] for s in cl.get_scene_list().scenes]
                inputs = sorted(i["inputName"] for i in cl.get_input_list().inputs)
                msg = f"Connected to OBS {version}: {len(scenes)} scenes, {len(inputs)} sources"
                ok = True
            except Exception as e:
                msg, ok, inputs = f"Could not connect: {e}", False, None
            self.root.after(0, lambda: self._obs_result(msg, ok, inputs))

        threading.Thread(target=work, daemon=True).start()

    def _obs_result(self, msg, ok, inputs):
        color = C["preview"] if ok else C["program"]
        self.obs_status.configure(text=msg, text_color=color)
        self.obs_hint.configure(text=msg, text_color=color)
        if inputs is not None:
            for cb in self.source_boxes:
                cb.configure(values=[""] + inputs)
        self._print(msg)

    # ------------------------------------------------------------------ live display
    def _print(self, line: str):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 2000:
            self.log.delete("1.0", "400.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _absorb(self, line: str):
        ev = parse_line(line)
        if ev is None:
            return
        if ev["kind"] in ("switch", "state"):
            self.live["scores"].update(ev["scores"])
        if ev["kind"] == "switch":
            self.live["on_air"] = ev["on_air"]
            who = " + ".join(ev["on_air"])
            self.live["history"].insert(0, (time.strftime("%H:%M:%S"), who, ev["target"] == "SPLIT"))
            del self.live["history"][7:]
        elif ev["kind"] == "state" and ev["current"] != "SPLIT":
            self.live["on_air"] = [ev["current"]] if ev["current"] in PLAYER_IDS else self.live["on_air"]
        elif ev["kind"] == "in_game" and ev["player"]:
            self.live["in_game"].add(ev["player"])
        elif ev["kind"] == "obs":
            self.live["obs"] = ev["ok"]

    def _render(self):
        running = self.group.running()
        names = {p.id: (p.name, p.scene) for p in self.cfg.players}
        for pid, tile in self.tiles.items():
            if pid in names:
                tile.render(names[pid][0], names[pid][1], running and pid in self.live["on_air"],
                            pid in self.live["in_game"], self.live["scores"].get(pid) if running else None, running)
        n = len(self.cfg.players)
        role = "Streaming PC" if self.local.role == "streamer" else f"Player PC {self.local.player_id}"
        if running:
            obs = {True: "OBS connected", False: "OBS unreachable", None: "connecting to OBS…"}[self.live["obs"]]
            if self.local.role != "streamer":
                obs = "agent running"
            self.control_sub.configure(text=f"Live · {n} players · {obs}", text_color=C["text"])
            self.btn_go.configure(text="Stop directing", fg_color=C["program"], hover_color="#EE6A6E",
                                  text_color="#FFFFFF")
            self.brand_tally.configure(fg_color=C["program"])
        else:
            self.control_sub.configure(text=f"Stopped · {n} players · {role}", text_color=C["muted"])
            self.btn_go.configure(text="Start directing", fg_color=C["action"], hover_color=C["action_hover"],
                                  text_color=C["on_action"])
            self.brand_tally.configure(fg_color=C["idle"])
        for w in self.hist_box.winfo_children():
            w.destroy()
        if not self.live["history"]:
            self.label(self.hist_box, "Camera cuts will show up here.", self.f.small,
                       C["faint"]).pack(fill="x")
        for t, who, split in self.live["history"]:
            row = ctk.CTkFrame(self.hist_box, fg_color="transparent")
            row.pack(fill="x", pady=1)
            self.label(row, t, self.f.small, C["faint"], width=70).pack(side="left")
            self.label(row, ("Split screen " if split else "Camera ") + who, self.f.small).pack(side="left")

    def _poll(self):
        changed = False
        try:
            for _ in range(400):
                line = self.group.lines.get_nowait()
                self._absorb(line)
                if not is_noise(line):
                    self._print(line)
                changed = True
        except queue.Empty:
            pass
        was = getattr(self, "_was_running", False)
        now = self.group.running()
        if changed or was != now:
            self._render()
        self._was_running = now
        self.root.after(self.POLL_MS, self._poll)

    def _on_close(self):
        self.group.stop()
        for g in self.tools.values():
            g.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()
