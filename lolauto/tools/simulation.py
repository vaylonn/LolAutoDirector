#!/usr/bin/env python3
"""End-to-end simulation WITHOUT the game or OBS: 2 fake Riot servers + 2 agents + the director in dry-run.

    python -m lolauto simulate            # ~66 s, prints the decisions and checks the scenario

Expected scenario: B (fight + kill) → A (fight) → B (A is dead) → A (start of a shared fight)
with no SPLIT (same fight) → SPLIT on two separate fights → back to a single POV.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time

from lolauto.paths import BASE, cli
PORT_A, PORT_B, UDP = 12999, 13000, 15005
DURATION = 66
EXPECTED = ["POV_B", "POV_A", "POV_B", "POV_A", "SPLIT"]
# 34-40 s: SHARED fight (Alice's kill with Bob's assist) → no SPLIT (split_mode = separate_fights)
# 50-56 s: two SEPARATE fights → SPLIT


def spawn(args, **kw):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(cli(*args), cwd=BASE, text=True, encoding="utf-8", errors="replace",
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags, **kw)


def main(argv=None) -> int:
    procs = [spawn(["mock", "--who", "A", "--port", str(PORT_A)]),
             spawn(["mock", "--who", "B", "--port", str(PORT_B)])]
    time.sleep(0.7)
    director = spawn(["director", "--dry-run", "--listen-port", str(UDP)])
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

    # format: "SWITCH → B (POV_B) | A=.. B=..", extract the scene name
    scenes = [m.group(1).split()[0] for ln in lines if (m := re.search(r"SWITCH → \w+ \(([^)]+)\)", ln))]
    print("\nObserved sequence:", scenes[:8])
    # Processes start in a variable order: check that the expected sequence appears IN ORDER
    # (as a subsequence) and that there is a single SPLIT (none during the shared fight).
    it = iter(scenes)
    ok = all(any(s == e for s in it) for e in EXPECTED) and scenes.count("SPLIT") == 1
    print("RESULT:", "OK ✅" if ok else f"FAILED ❌ (expected {EXPECTED})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
