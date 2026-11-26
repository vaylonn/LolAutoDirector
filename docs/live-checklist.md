# Live checklist

About 10 minutes. Go in order: each step assumes the previous one is done.

## 15 minutes before

- [ ] Every player PC is on **Ethernet** (and on power for a laptop)
- [ ] The streaming PC's IP has not changed (`ipconfig`): it matches *Ce PC › IP du PC qui diffuse*
- [ ] Every PC has the same `config.toml` (same IP and token), and each player has their own letter
- [ ] No pending update for League, OBS, Windows or macOS

## OBS on the streaming PC

- [ ] OBS is open and its WebSocket server is on
- [ ] In the app, *OBS › Tester la connexion* succeeds and the `POV_x` and `SPLIT` scenes are listed
- [ ] Each remote `POV_x` shows the player's NDI image, not black, not stuttering
- [ ] **Stream delay** is on (ranked games: stream sniping)
- [ ] Audio levels checked: mic, game, teammates (their audio comes with the NDI sources)
- [ ] A managed scene (`POV_A`…) is on air; on any other scene the director waits

## Start the director (5 minutes before)

- [ ] Each teammate: agent started (*Démarrer la régie* in the app, or `mac/start.command`)
- [ ] Streaming PC: **Régie › Démarrer la régie** shows *OBS connecté*
- [ ] No error in the app's log

## First two minutes of the game

- [ ] Every camera tile turns green (player in game)
- [ ] The log shows the enemy champions, then their portraits loaded
- [ ] Switch a scene by hand in OBS: the director pauses, then takes over again 20 s later
- [ ] First fight: the camera goes to the right player

## During the stream

- [ ] OBS CPU and game FPS are fine (otherwise *Vision* › read every 0.5 s)
- [ ] Teammates' images are smooth
- [ ] No bursts of cuts

## When to take over by hand

Decide this **before** going live.

| Signal | Action |
|---|---|
| More than one cut every 3 s for a minute | *Arrêter la régie* (stop): OBS stays on the current scene, switch by hand |
| *OBS injoignable* (OBS unreachable) keeps showing | Check OBS and its WebSocket, otherwise switch by hand |
| A tile stays grey during the game | Restart that player's agent; check their firewall, IP, token and letter |
| A player's image is black or frozen | Restart OBS on their PC; meanwhile turn that player off in *Joueurs* |
| The game lags because of the vision | Turn the vision off in *Vision* and restart the director |

Stopping the director stops neither the stream nor OBS. Click *Démarrer la régie* again to resume.

## After the stream

- [ ] *Arrêter la régie* on every PC
- [ ] If something was odd, keep `logs/director.log` and note the time and what happened
