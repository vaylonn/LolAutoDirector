#!/usr/bin/env python3
"""End-to-end simulation WITHOUT the game or OBS: 2 fake Riot servers + 2 agents + the director in dry-run.

    python -m lolauto simulate            # ~66 s, prints the decisions and checks the scenario
    python -m lolauto simulate --obs      # same, and OBS really switches between POV_A, POV_B and SPLIT

Expected scenario: B (fight + kill) → A (fight) → B (A is dead) → A (start of a shared fight)
with no SPLIT (same fight) → SPLIT on two separate fights → back to a single POV.
"""

from __future__ import annotations

import argparse
import re
import socket
import subprocess
import threading
import time

from lolauto import config as cfgmod
from lolauto.paths import BASE, cli


def free_port(kind=socket.SOCK_STREAM) -> int:
    """A port nobody uses right now, so two simulations (or a live director) never collide."""
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
DURATION = 66
EXPECTED = ["B", "A", "B", "A", "SPLIT"]
# 34-40 s: SHARED fight (Alice's kill with Bob's assist) → no SPLIT (split_mode = separate_fights)
# 50-56 s: two SEPARATE fights → SPLIT


def spawn(args, **kw):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(cli(*args), cwd=BASE, text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags, **kw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Simulated game, no LoL needed")
    ap.add_argument("--obs", action="store_true", help="drive OBS for real (players A and B of config.toml)")
    args = ap.parse_args(argv)
    mode = ["--dry-run"]
    if args.obs:
        ids = cfgmod.load().player_ids
        if "A" in ids and "B" in ids:
            mode = ["--no-vision", "--take-over"]
            print("OBS mode: OBS switches between the scenes of players A and B", flush=True)
        else:
            print("OBS mode needs players A and B in config.toml: running without OBS", flush=True)

    PORT_A, PORT_B, UDP = free_port(), free_port(), free_port(socket.SOCK_DGRAM)
    procs = [spawn(["mock", "--who", "A", "--port", str(PORT_A)]),
             spawn(["mock", "--who", "B", "--port", str(PORT_B)])]
    time.sleep(0.7)
    director = spawn(["director", *mode, "--listen-port", str(UDP)])
    procs.append(director)
    time.sleep(0.5)
    for who, port in (("A", PORT_A), ("B", PORT_B)):
        procs.append(spawn(["agent", "--id", who, "--api-url", f"http://127.0.0.1:{port}",
                            "--director-host", "127.0.0.1", "--director-port", str(UDP)]))

    lines: list[str] = []

    def reader():
        for line in director.stdout:
            line = line.rstrip()
            lines.append(line)
            print(line, flush=True)

    threading.Thread(target=reader, daemon=True).start()
    try:
        time.sleep(DURATION)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()

    # format: "SWITCH → B (POV_B) | A=.. B=..", extract the target (A, B or SPLIT)
    targets = [m.group(1) for ln in lines if (m := re.search(r"SWITCH → (\w+)", ln))]
    print("\nObserved sequence:", targets[:8])
    expected, splits = EXPECTED, 1
    if any("SPLIT mode disabled" in ln for ln in lines):     # no SPLIT scene in OBS
        expected, splits = [t for t in EXPECTED if t != "SPLIT"], 0
        print("No SPLIT scene in OBS: split screen skipped (OBS › Prepare scenes creates it)")
    # Processes start in a variable order: check that the expected sequence appears IN ORDER
    # (as a subsequence) and that there is a single SPLIT (none during the shared fight).
    it = iter(targets)
    ok = all(any(s == e for s in it) for e in expected) and targets.count("SPLIT") == splits
    print("RESULT:", "OK ✅" if ok else f"FAILED ❌ (expected {expected})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
