# Player PC on a Mac

For each teammate on a Mac whose screen should appear on the stream. About 15 minutes the first time.

Your Mac does two things:

1. **OBS + NDI** sends your game image to the streaming PC over the network.
2. **The agent** reads your game (health, kills, deaths…) and sends a small score to the streaming PC.

You do not stream anything yourself. The streamer gives you your **player letter** (B, C, D or E) and their
`config.toml` file.

## 1. Network and power

- Plug the Mac into the same router or switch as the streaming PC with an **Ethernet cable** (a USB-C adapter
  works). Wi-Fi makes the image stutter.
- Keep a laptop **on power**: on battery, macOS may slow it down.

## 2. Send your screen: OBS + NDI

1. Install [OBS Studio](https://obsproject.com) (28 or later), the NDI runtime (6.0.1 or later), then the
   [DistroAV](https://github.com/DistroAV/DistroAV) plugin. Restart OBS.
2. *OBS › Settings › Video*: base **and** output resolution **1920×1080** (not the Retina resolution).
3. Add a **macOS Screen Capture** source on the League of Legends window. Play in **borderless** mode.
4. *System Settings › Privacy & Security*:
   - **Screen Recording**: allow OBS;
   - **Local Network**: allow OBS, or the streaming PC will not see your image.
5. *Tools › DistroAV NDI Output Settings*: tick *Main Output* and give it a name, e.g. `MACBOOK`.

Tell the streamer the name you picked: they select it as your *Video source* in their app.

## 3. Install the agent (once)

1. On GitHub, *Code › Download ZIP*, then unzip it.
2. Copy the streamer's **`config.toml`** into the unzipped folder (next to `README.md`).
3. If Python 3 is missing, run `xcode-select --install` in Terminal.
4. In the `mac` folder, **right-click › Open** on `install.command`.
   If macOS refuses to run it, open Terminal in the project folder and run `chmod +x mac/*.command`.

## During the stream

1. Open OBS first: the NDI output starts on its own.
2. Double-click **`mac/start.command`**. It asks for your letter the first time and remembers it.
   Keep the Terminal window open: it also stops the Mac from going to sleep. You can start it before the game;
   it waits on its own.
3. Launch your game. The window shows `Game detected`.

To stop, close the Terminal window.

## If something goes wrong

| Symptom | What to do |
|---|---|
| The streamer cannot see `MACBOOK` | *Local Network* permission for OBS, Ethernet, same network |
| Your image is black on their side | *Screen Recording* permission for OBS, then restart OBS |
| The image stutters | Ethernet, OBS at 1080p (not Retina), Mac on power |
| The camera never comes to you | Use the streamer's exact `config.toml` (IP and token), and check your letter |
| `Game detected` never shows | Normal outside a game: the game API only exists during a match |
| The Mac runs hot or slow | Send NDI at 1080p30 or 720p60, cap League's FPS |

Your log is in `logs/agent_<letter>.log`. Send it to the streamer if something is off.
