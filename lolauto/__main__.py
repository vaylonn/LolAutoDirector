"""Point d'entrée unique :  python -m lolauto <commande>  (ou LoLAutoDirector.exe <commande>).

Sans commande : ouvre le lanceur graphique.

    gui              lanceur graphique (défaut)
    director         directeur (PC qui diffuse) — pilote OBS
    agent            agent d'un PC de jeu (--id A..E)
    simulate         simulation complète sans jeu ni OBS (~66 s)
    mock             faux serveur de l'API du jeu (tests)
    obs-setup        prépare les scènes OBS (--split, --game-capture ID)
    vision-sources   garde les captures de la vision actives dans toutes les scènes
    vision-debug     captures annotées de la minimap (calibration)
"""

from __future__ import annotations

import sys


def _commands():
    from . import agent, director
    from .outils import mock_api, obs_setup, simulation, vision_debug, vision_sources

    def gui(argv):
        from . import launcher
        return launcher.main(argv)

    return {
        "gui": gui,
        "director": director.main,
        "agent": agent.main,
        "simulate": simulation.main,
        "mock": mock_api.main,
        "obs-setup": obs_setup.main,
        "vision-sources": vision_sources.main,
        "vision-debug": vision_debug.main,
    }


def _utf8_console() -> None:
    """Windows : la console / les tubes sont en cp1252 et l'exécutable ignore PYTHONIOENCODING.
    Sans ça, le premier « → » ou « é » écrit fait planter le programme."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv=None) -> int:
    _utf8_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    cmds = _commands()
    if not argv:
        return cmds["gui"]([]) or 0
    if argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = cmds.get(argv[0])
    if cmd is None:
        print(f"Commande inconnue : {argv[0]!r}\n{__doc__}")
        return 2
    return cmd(argv[1:]) or 0


if __name__ == "__main__":
    raise SystemExit(main())
