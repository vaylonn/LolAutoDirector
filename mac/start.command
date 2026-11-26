#!/bin/bash
# Starts this player's agent. `caffeinate` keeps the Mac awake while it runs. Close the window to stop.
# Usage: double-click (asks for your letter once and remembers it), or: mac/start.command B
cd "$(dirname "$0")/.." || exit 1
[ -x .venv/bin/python ] || { echo "Run mac/install.command first"; read -r; exit 1; }
[ -f config.toml ] || { echo "config.toml missing: copy the streamer's config.toml into this folder"; read -r; exit 1; }
source .venv/bin/activate
mkdir -p logs
ID="$1"
[ -z "$ID" ] && [ -f joueur.txt ] && ID="$(cat joueur.txt)"
[ -z "$ID" ] && read -r -p "Your player letter (B, C, D or E): " ID
ID="$(echo "$ID" | tr '[:lower:]' '[:upper:]')"
echo "$ID" > joueur.txt
exec caffeinate -dimsu python -m lolauto agent --id "$ID" --log-file "logs/agent_$ID.log"
