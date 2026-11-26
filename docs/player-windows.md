# Player PC on Windows

For each teammate whose screen should appear on the stream. About 15 minutes the first time.

Your PC does two things:

1. **OBS + NDI** sends your game image to the streaming PC over the network.
2. **The agent** reads your game (health, kills, deaths…) and sends a small score to the streaming PC.

You do not stream anything yourself. The streamer gives you your **player letter** (B, C, D or E) and their
`config.toml` file.

## 1. Network

Plug your PC into the same router or switch as the streaming PC with an **Ethernet cable**. Wi-Fi makes the
image stutter.

## 2. Send your screen: OBS + NDI

1. Install [OBS Studio](https://obsproject.com) (28 or later), then the [DistroAV](https://github.com/DistroAV/DistroAV)
   plugin, which also installs the NDI runtime. Restart OBS.
2. *Settings › Video*: set the **output** resolution to **1920×1080**, even if your screen is bigger.
3. Add a **Game Capture** source › mode *Capture specific window* › League of Legends.
4. *Tools › DistroAV NDI Output Settings*: tick *Main Output* and give it a name, e.g. `PC LEO`.
5. If the Windows firewall asks about OBS, allow it on **private networks**.

Tell the streamer the name you picked: they select it as your *Video source* in their app.

## 3. Start the agent

1. Download **`LoLAutoDirector.exe`** from the [latest release](https://github.com/vaylonn/LolAutoDirector/releases/latest).
2. Put it in a folder together with the streamer's **`config.toml`**.
3. Open it. Windows SmartScreen may warn about an unknown publisher: *More info › Run anyway*.
4. Page **This PC**:
   - *This PC is the*: **Player PC**;
   - *Player*: your letter;
   - *Streaming PC IP* and *Token* are already filled in from `config.toml`.
5. Click **Save**.

## During the stream

1. Open OBS first: the NDI output starts on its own.
2. In the app, page **Control room** › **Start directing**. Keep the window open.
3. Launch your game. The log shows `Game detected` when it starts.

## If something goes wrong

| Symptom | What to do |
|---|---|
| The streamer cannot see your NDI source | Same network, Ethernet, OBS allowed on private networks in the firewall |
| Your image is black on their side | In your Game Capture, try *Capture any fullscreen application*; start OBS before the game |
| The camera never comes to you | Use the streamer's exact `config.toml` (IP and token), and check your letter |
| `Game detected` never shows | Normal outside a game: the game API only exists during a match |

Your log is in `logs\agent_<letter>.log` next to the exe. Send it to the streamer if something is off.
