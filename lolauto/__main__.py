"""Single entry point:  python -m lolauto <command>  (or LoLAutoDirector.exe <command>).

Without a command, opens the app.

    gui              the app (default)
    director         the director (streaming PC), drives OBS
    agent            a gaming PC's agent (--id A..E)
    simulate         full simulated game, no game or OBS needed (~66 s)
    mock             fake game API server (testing)
    obs-setup        creates the OBS scenes (--split, --game-capture ID)
    vision-sources   keeps the vision's captures active in every scene
    vision-debug     annotated minimap captures (calibration)
"""

from __future__ import annotations

import sys


def _commands():
    from . import agent, director
    from .tools import mock_api, obs_setup, simulation, vision_debug, vision_sources

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
    """Windows consoles and pipes default to cp1252 and the executable ignores PYTHONIOENCODING:
    without this, the first non-ASCII character printed would crash the program."""
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
        print(f"Unknown command: {argv[0]!r}\n{__doc__}")
        return 2
    return cmd(argv[1:]) or 0


if __name__ == "__main__":
    raise SystemExit(main())
