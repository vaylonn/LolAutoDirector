#!/bin/bash
# One-time setup on a Mac player PC: creates .venv and installs the agent (no OBS control needed here).
cd "$(dirname "$0")/.." || exit 1
if ! command -v python3 >/dev/null; then
  echo "python3 not found. Install it with: xcode-select --install   (or: brew install python)"; exit 1
fi
python3 -m venv .venv && source .venv/bin/activate || exit 1
python -m pip install --upgrade pip
pip install -r requirements-player.txt || { echo "Install FAILED"; exit 1; }
echo
echo "Install OK. Put the streamer's config.toml in this folder, then double-click mac/start.command"
read -r -p "Press Enter to close."
