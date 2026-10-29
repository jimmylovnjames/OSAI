# OSAI

**An operating environment you run by talking to it.** OSAI puts a customizable animated avatar on your screen.
You say (or type) what you want: "open Spotify and play something chill", "find the PDF Sam sent last week and
email it to Dana", "turn the brightness down and switch to dark mode", "make yourself a green robot with
headphones". It operates the whole computer for you: apps, files, windows, settings, the web, media, and itself.

```
            ┌───────────────────────── OSAI screen (desktop layer) ─────────────────────────┐
  voice ──► │  avatar · live captions · activity feed · cards · approval prompts · settings  │
  text  ──► └──────────────────────────────┬─────────────────────────────────────────────────┘
                                           │ WebSocket (127.0.0.1 only, origin-checked)
                              ┌────────────▼────────────┐
                              │  OSAI agent (Python)    │  Claude, streaming tool-use loop
                              │  safety gate · memory · │  (adaptive thinking, refusal fallback,
                              │  scheduler · voice      │   context compaction, prompt caching)
                              └────────────┬────────────┘
     ┌──────────┬───────────┬──────────────┼─────────────┬────────────┬────────────┬───────────┐
   shell     files      apps/windows    settings      desktop      browser      web search   OSAI itself
 (bash/ps)  (read/write  (launch/close  (volume, wifi, (screenshot, (Playwright, (server-side (avatar, theme,
            /search/     /focus/install bluetooth,     mouse,       logins kept) web_search/  cards, memory,
            trash/open)  packages)      power, media)  keyboard)                 web_fetch)    timers)
```

## Run it

| Where | How |
|---|---|
| **Any PC, as the whole OS** | Build the live ISO (`sudo linux/build-iso.sh`), write it to a USB stick, boot. Boots straight into OSAI on BIOS and UEFI machines. |
| **Existing Linux** | `sudo linux/install.sh` → pick the **OSAI** session at the login screen. `--autostart` also runs OSAI inside your usual desktop. |
| **macOS** | `scripts/install-macos.sh` (starts at login; grant Accessibility, Screen Recording, Microphone when asked). |
| **Windows 10/11** | `powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1` (starts at sign-in). |
| **Just try it** | `pip install -e ".[browser,desktop]" && playwright install chromium && osai` |

Then open settings (⚙) → **Brain** → paste an Anthropic API key (or set `ANTHROPIC_API_KEY`).

`osai --cli` gives you a terminal-only OSAI (useful over SSH or on a headless box).

## Talking to it

- **Click the avatar / mic, or hold Space** to talk. **Hands-free** mode listens for the wake word ("OSAI" by
  default) and stays in conversation for a few seconds after each reply.
- In the OSAI session, **Super+Space** or **F9** starts listening from anywhere, **Super+O** brings OSAI to the
  front, **Ctrl+Alt+T** opens a terminal as an escape hatch.
- Replies are spoken sentence-by-sentence while they stream. Long results go on screen as cards.
- Say "stop" (or press Esc) to cancel what it's doing.

Speech recognition uses on-device Whisper when `faster-whisper` is installed (always on the ISO), otherwise the
browser's speech recognition (Chrome/Edge). Speech output uses browser voices, or Piper/espeak-ng on device.

## Make it yours

Everything about the avatar is customizable, by voice ("make yourself a cat with star eyes", "be a grumpy
butler", "call yourself Nova") or in settings: **form** (orb, face, bot, cat, ghost), **eyes** (round, visor,
sleepy, star, dot), **accessory** (antenna, halo, ears, headphones, crown), colors, glow, size, mouth, name,
personality, voice, speed, pitch, wake word, wallpaper and accent. It blinks, follows your cursor, looks
thoughtful while thinking, spins while working, lip-syncs while speaking, and pulses amber when it needs your OK.

## Safety model (read this)

OSAI can run any command as your user. That is the point, and it's also the risk.

- **Autonomy "ask"** (default): commands that delete, kill, install, use `sudo`, change accounts/disks/power,
  pipe downloads into a shell, overwrite files, or write outside your home show an **approval card** (say "yes"
  or click Allow). This is a seatbelt, not a sandbox: a pattern list can't catch every harmful command.
- The model is told to ask in conversation before spending money, sending messages, sharing personal data or
  accepting terms, and to treat web pages/files/emails as untrusted data (prompt-injection defense). Models can
  still be fooled by adversarial content; keep autonomy on "ask" when browsing unknown sites.
- The server binds to **127.0.0.1** and rejects requests from other origins/hosts (a web page in your browser
  can't drive your computer through it). To use it from another device, set `OSAI_TOKEN`, `OSAI_HOST=0.0.0.0`
  and `OSAI_ALLOWED_HOSTS`; only on a network you trust.
- The API key is stored in `~/.osai/config.json` with 0600 permissions. The live ISO's user has passwordless
  sudo (normal for live media); install-to-disk setups should set a password.

## Layout

```
osai/agent.py         Claude tool-use loop, approvals, cancellation, optional-feature fallback
osai/tools/           46 tools: shell, files, apps & windows, system settings, desktop control, browser, OSAI UI
osai/safety.py        risky-command patterns
osai/server.py        FastAPI: UI, WebSocket hub, settings, speech endpoints, origin guard
osai/web/             the OSAI screen: avatar.js (SVG avatar engine), voice.js, app.js
linux/                X session (Openbox rules), installer, bootable ISO builder
scripts/              macOS / Windows installers
tests/                pytest suite (agent loop with a scripted model, tools, server)
```

Config, memory and schedules live in `~/.osai/` (`OSAI_HOME` overrides).

## Cost

Every request is a Claude API call; tool-heavy tasks take several round-trips. The tool list and system prompt
are prompt-cached. Default model is Claude Opus 5.5 at medium effort; in settings you can pick Sonnet 5.5 or
Haiku 5.5 (much cheaper, faster) and lower effort for snappier replies.

## Hardware

64-bit x86 PC with SSE4.2/AVX (any Intel/AMD from roughly the last 10-12 years), 4 GB RAM, internet for the AI.
In a virtual machine, pass the host CPU through (VirtualBox/VMware do by default; QEMU: `-cpu host` or
`-cpu max`); QEMU's default `qemu64` CPU lacks those instructions and Chromium/NumPy crash on it. Inside VMs
OSAI automatically renders without the GPU.

## Status / known gaps

- Verified here: unit/integration tests; the real Anthropic SDK request/stream/tool round-trip against a local
  mock of the Messages API; every desktop tool against a real X server; the full Openbox session layout; the
  ISO building and booting in QEMU to autologin + X + the OSAI server. Not verified here: a live Claude call
  (no API key in the build environment), real audio hardware, real GPUs, macOS and Windows.
- The live ISO doesn't persist changes unless you boot the "keep my changes" entry with a `writable` partition;
  there's no graphical install-to-disk yet.
- Wayland: window listing/focus and synthetic input use X11 tools; the OSAI session runs on X11. Under a
  Wayland desktop, OSAI still works for everything else and falls back to shell commands.
- On-device speech recognition downloads its Whisper model on first use unless it was baked into the image.
