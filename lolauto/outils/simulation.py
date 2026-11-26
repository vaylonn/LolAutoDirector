#!/usr/bin/env python3
"""Simulation bout en bout SANS jeu ni OBS : 2 faux serveurs Riot + 2 agents + le directeur en dry-run.

    python -m lolauto simulate            # ~66 s, affiche les décisions et vérifie le scénario

Scénario attendu : B (combat+kill) → A (combat) → B (A est mort) → A (début du combat à deux)
sans SPLIT (même combat) → SPLIT sur deux combats séparés → retour sur un seul POV.
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
# 34-40 s : combat COMMUN (kill d'Alice avec assist de Bob) → pas de SPLIT (split_mode = combats_separes)
# 50-56 s : deux combats SÉPARÉS → SPLIT


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

    # forme : "SWITCH → B (POV_B) | A=.. B=.." ; on extrait le nom de scène
    scenes = [m.group(1).split()[0] for ln in lines if (m := re.search(r"SWITCH → \w+ \(([^)]+)\)", ln))]
    print("\nSéquence observée :", scenes[:8])
    # Les processus démarrent dans un ordre variable : on vérifie que la séquence attendue apparaît
    # DANS L'ORDRE (sous-séquence) et qu'il n'y a qu'un seul SPLIT (pas pendant le combat commun).
    it = iter(scenes)
    ok = all(any(s == e for s in it) for e in EXPECTED) and scenes.count("SPLIT") == 1
    print("RÉSULTAT :", "OK ✅" if ok else f"ÉCHEC ❌ (attendu {EXPECTED})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
