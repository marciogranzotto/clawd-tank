<p align="center">
  <img src="assets/app-icon.svg" width="128" height="128" alt="Clawd Tank App Icon">
</p>

<h1 align="center">Clawd Tank</h1>

A tiny desktop aquarium for your Claude Code sessions.

Clawd Tank is a notification display for Claude Code built on a [Waveshare ESP32-C6-LCD-1.47](https://s.click.aliexpress.com/e/_c4PGS55v) (320x172 ST7789). An animated pixel-art crab named Clawd lives on the screen, reacting to your coding session — alerting on new notifications, celebrating when you dismiss them, and sleeping when you're away.

**No hardware? No problem.** The simulator runs natively on macOS and ships bundled inside the Menu Bar app. Download it from [Releases](https://github.com/marciogranzotto/clawd-tank/releases) — no build tools needed.

<p align="center">
  <img src="assets/sim-recordings/clawd-multi-session.gif" alt="Multi-session display" width="480">
  <img src="assets/sim-recordings/clawd-notification.gif" alt="Clawd notification" width="480">
</p>

## How It Works

```
Claude Code hooks --> clawd-tank-notify --> daemon --> BLE --> ESP32-C6 display
                                                  \-> TCP --> Simulator (SDL2)
```

1. **Claude Code hooks** (`SessionStart`, `PreToolUse`, `PreCompact`, `Stop`, `StopFailure`, `Notification`, `UserPromptSubmit`, `SessionEnd`, `SubagentStart`, `SubagentStop`) fire on session events
2. **clawd-tank-notify** (`~/.clawd-tank/clawd-tank-notify`) forwards the event to the daemon via Unix socket
3. The **daemon** tracks per-session state, maps `tool_name` to animations, and sends JSON payloads to connected transports (BLE hardware, TCP simulator)
4. The **firmware** (or simulator) renders Clawd's tool-aware animation + notification cards on the LCD via LVGL

## Components

| Directory | What | Language |
|-----------|------|----------|
| `firmware/` | ESP-IDF firmware (LVGL UI, NimBLE GATT server, SPI display) | C |
| `simulator/` | Native macOS simulator — runs the same firmware code without hardware | C |
| `host/` | Background daemon, Claude Code hook handler, macOS menu bar app | Python |
| `tools/` | Sprite pipeline (SVG to PNG to RLE-compressed RGB565), GIF recorder, BLE debugging | Python |

## Hardware

- **Board**: [Waveshare ESP32-C6-LCD-1.47](https://s.click.aliexpress.com/e/_c4PGS55v)
- **Display**: 1.47" 320x172 ST7789V (SPI), 16-bit RGB565
- **SoC**: ESP32-C6FH8 (RISC-V, single core), 8MB flash, 512KB SRAM (no PSRAM)
- **RGB LED**: Onboard WS2812B on GPIO8 — flashes on incoming notifications
- **Connectivity**: BLE 5.0 (NimBLE, peripheral role)

## Quick Start

### Download (no hardware needed)

Grab the latest `.app` from [Releases](https://github.com/marciogranzotto/clawd-tank/releases), unzip, and drag to Applications. The app bundles the simulator — a borderless, resizable window shows Clawd on your desktop, driven by your Claude Code sessions.

On first launch: click the crab icon in the menu bar → **Install Claude Code Hooks**. Restart any running Claude Code sessions.

### Build from source — Simulator

```bash
brew install sdl2 cmake

cd simulator
cmake -B build && cmake --build build

# Interactive mode — opens a borderless, resizable SDL2 window
./build/clawd-tank-sim

# Interactive + TCP listener — daemon can connect and drive it
./build/clawd-tank-sim --listen

# Self-contained binary (no Homebrew SDL2 needed)
cmake -B build-static -DSTATIC_SDL2=ON && cmake --build build-static

# Headless mode — outputs PNG screenshots
./build/clawd-tank-sim --headless \
  --events 'connect; wait 500; notify "clawd-tank" "Waiting for input"; wait 2000; disconnect' \
  --screenshot-dir ./shots/ --screenshot-on-event
```

Interactive keys: `c` connect, `d` disconnect, `n` add notification, `1-8` dismiss, `x` clear, `s` screenshot, `z` sleep, `q` quit. The window is borderless and resizable — drag from center, resize from edges.

When `--listen` is active (default port 19872), the daemon can connect over TCP and drive the simulator with the same JSON protocol used over BLE, enabling the full Claude Code → daemon → display pipeline without hardware.

See [simulator/README.md](simulator/README.md) for full CLI reference and JSON scenario support.

### Firmware

Requires [ESP-IDF 5.3.2](https://docs.espressif.com/projects/esp-idf/en/v5.3.2/esp32c6/get-started/index.html) (bundled in `bsp/esp-idf/`, activated via direnv).

```bash
cd firmware
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

### macOS Menu Bar App

The menu bar app bundles the daemon and simulator with a status bar UI. It manages two independent transports — **BLE** (hardware) and **Simulator** (software) — each with their own submenu for enable/disable and connection status.

```bash
# Run from source
cd host && python -m clawd_tank_menubar

# Build and install (builds static simulator, py2app, bundles binary)
cd host && ./build.sh --install

# Or build manually
cd host && pip install py2app && python setup.py py2app
cp ../simulator/build-static/clawd-tank-sim "dist/Clawd Tank.app/Contents/MacOS/"
open "dist/Clawd Tank.app"
```

**Menu features:**
- BLE and Simulator transport submenus with enable/disable toggles
- Simulator window controls: Show/Hide, Always on Top
- Brightness slider, session timeout picker
- Claude Code hook installer
- Version display (tag or branch+N@sha)
- Launch at Login with stale plist detection

On launch, the app automatically installs a hook handler script to `~/.clawd-tank/clawd-tank-notify`. To connect it to Claude Code, click **"Install Claude Code Hooks"** in the menu bar dropdown — this adds the required hooks to `~/.claude/settings.json`. Restart any running Claude Code sessions for hooks to take effect.

Logs are written to `~/Library/Logs/ClawdTank/clawd-tank.log`.

Pre-built releases are available on the [Releases](https://github.com/marciogranzotto/clawd-tank/releases) page.

### Host Daemon (standalone)

The daemon can also run standalone without the menu bar app:

```bash
cd host
pip install -r requirements.txt

# Run daemon with simulator transport
python -m clawd_tank_daemon --sim

# Run daemon with simulator only (no BLE)
python -m clawd_tank_daemon --sim-only
```

The daemon auto-starts on the first hook event. Logs at `~/.clawd-tank/daemon.log`.

## Features

- **Multi-session display** — up to 4 concurrent Claude Code sessions shown as individual animated Clawd sprites, each with their own working animation. New sessions walk in, exiting sessions burrow away
- **Tool-aware animations** — Clawd shows distinct animations based on which tool Claude is using: debugger (Read/Grep), typing (Edit/Write), building (Bash), wizard (WebSearch), conducting (Agent/subagents), beacon (LSP/MCP)
- **Session tracking** — daemon tracks per-session state with priority-based display resolution, staleness eviction, and subagent lifecycle tracking
- **Session persistence** — session state survives daemon restarts, so relaunching the app immediately shows the correct animation for running sessions
- **Time display** — synced from host over BLE on connect (no WiFi/NTP needed)
- **RGB LED flash** — onboard WS2812B cycles through colors on new notifications
- **RLE sprite compression** — all sprite assets compressed ~14:1 (13MB raw → ~900KB)
- **Bundled simulator** — macOS `.app` ships with the simulator binary, no hardware needed. Borderless resizable window with integer pixel scaling
- **Multi-transport** — daemon supports BLE (hardware) and TCP (simulator) transports simultaneously, independently enable/disable
- **Simulator bridge** — full pipeline works without hardware via `--listen` flag and TCP. Window show/hide/pinned controlled over TCP
- **Static SDL2 build** — `STATIC_SDL2=ON` produces a self-contained binary with zero external dependencies
- **Auto-reconnect** — daemon replays active notifications and display state after reconnect on any transport
- **Config over BLE/TCP** — brightness and session timeout adjustable via config characteristic or TCP
- **macOS menu bar app** — transport submenus with colored status indicators, simulator window controls, brightness slider, session timeout, hook installer, version display, launch-at-login

## Clawd's Moods

### Tool-Aware Animations

Clawd's animation reflects which tool Claude is currently using. Each session gets its own Clawd sprite with a tool-specific animation:

| Animation | Tools | |
|-----------|-------|---|
| **Debugger** | `Read`, `Grep`, `Glob` — searching/inspecting code | ![Debugger](assets/sim-recordings/clawd-debugger.gif) |
| **Typing** | `Edit`, `Write`, `NotebookEdit` — writing code | ![Typing](assets/sim-recordings/clawd-typing.gif) |
| **Building** | `Bash` — running shell commands | ![Building](assets/sim-recordings/clawd-building.gif) |
| **Conducting** | `Agent` / active subagents — orchestrating work | ![Conducting](assets/sim-recordings/clawd-conducting.gif) |
| **Wizard** | `WebSearch`, `WebFetch` — conjuring web knowledge | ![Wizard](assets/sim-recordings/clawd-wizard.gif) |
| **Beacon** | `LSP`, MCP tools (`mcp__*`) — communicating with services | ![Beacon](assets/sim-recordings/clawd-beacon.gif) |

### Session States

| State | When | |
|-------|------|---|
| **Multi-session** | 2+ concurrent sessions, each with individual tool animations | ![Multi-session](assets/sim-recordings/clawd-multi-session.gif) |
| **Thinking** | User submitted a prompt, Claude is reasoning | ![Thinking](assets/sim-recordings/clawd-thinking.gif) |
| **Confused** | Claude has been waiting 60s+ for user input | ![Confused](assets/sim-recordings/clawd-confused.gif) |
| **Sweeping** | Context compaction (PreCompact) — oneshot | ![Sweeping](assets/sim-recordings/clawd-sweeping.gif) |

### HUD Badges

| Badge | When | |
|-------|------|---|
| **Subagent counter** | Active subagents — mini-crab icon with `×N` count (top-left) | ![Subagents](assets/sim-recordings/clawd-hud-subagents.gif) |
| **Overflow (wide)** | 5+ sessions — `+N` badge shows extra sessions beyond the 4 visible (top-right) | ![Overflow wide](assets/sim-recordings/clawd-overflow-wide.gif) |
| **Overflow (narrow)** | Notification panel open — `×N` badge shows total session count (top-right of scene) | ![Overflow narrow](assets/sim-recordings/clawd-overflow-narrow.gif) |

### Notification & Lifecycle

| State | When | |
|-------|------|---|
| **Idle** | Connected, no notifications — Clawd hangs out, full-screen with clock | ![Idle](assets/sim-recordings/clawd-idle.gif) |
| **Alert** | New notification arrives — Clawd shifts left, cards appear, LED flashes | ![Alert](assets/sim-recordings/clawd-notification.gif) |
| **Happy** | Notifications dismissed | ![Happy](assets/sim-recordings/clawd-happy.gif) |
| **Sleeping** | No active sessions — all sessions ended or evicted | ![Sleeping](assets/sim-recordings/clawd-sleeping.gif) |
| **Disconnected** | No BLE connection — "No connection" message | ![Disconnected](assets/sim-recordings/clawd-disconnected.gif) |

## Animation Gallery

Every Clawd animation, without the scene background. Previews are rendered from the SVG sources in [`assets/svg-animations/`](assets/svg-animations/) with `python tools/svg2slack_emoji.py` (transparent GIFs, max 128px).

### On the device (19)

<table>
  <tr>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-idle-living.gif" alt="Idle" height="96"><br><b>Idle</b><br><sub>Connected and idle: a session finished its turn, no notifications pending</sub><br><sub><a href="assets/svg-animations/clawd-idle-living.svg"><code>clawd-idle-living.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-notification.gif" alt="Alert" height="96"><br><b>Alert</b><br><sub>New notification card, or Claude is waiting on you (<code>AskUserQuestion</code> / permission prompt)</sub><br><sub><a href="assets/svg-animations/clawd-notification.svg"><code>clawd-notification.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-happy.gif" alt="Happy" height="96"><br><b>Happy</b><br><sub>Notifications dismissed (oneshot)</sub><br><sub><a href="assets/svg-animations/clawd-happy.svg"><code>clawd-happy.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-sleeping.gif" alt="Sleeping" height="96"><br><b>Sleeping</b><br><sub>No active sessions</sub><br><sub><a href="assets/svg-animations/clawd-sleeping.svg"><code>clawd-sleeping.svg</code></a></sub></td>
  </tr>
  <tr>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-disconnected.gif" alt="Disconnected" height="96"><br><b>Disconnected</b><br><sub>No connection to the host</sub><br><sub><a href="assets/svg-animations/clawd-disconnected.svg"><code>clawd-disconnected.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-thinking.gif" alt="Thinking" height="96"><br><b>Thinking</b><br><sub>Prompt submitted, Claude is reasoning</sub><br><sub><a href="assets/svg-animations/clawd-working-thinking.svg"><code>clawd-working-thinking.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-typing.gif" alt="Typing" height="96"><br><b>Typing</b><br><sub><code>Edit</code>, <code>Write</code>, <code>NotebookEdit</code> and unmapped tools · 1 working session on v1</sub><br><sub><a href="assets/svg-animations/clawd-working-typing.svg"><code>clawd-working-typing.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-debugger.gif" alt="Debugger" height="96"><br><b>Debugger</b><br><sub><code>Read</code>, <code>Grep</code>, <code>Glob</code></sub><br><sub><a href="assets/svg-animations/clawd-working-debugger.svg"><code>clawd-working-debugger.svg</code></a></sub></td>
  </tr>
  <tr>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-building.gif" alt="Building" height="96"><br><b>Building</b><br><sub><code>Bash</code> · 3+ working sessions on v1</sub><br><sub><a href="assets/svg-animations/clawd-working-building.svg"><code>clawd-working-building.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-wizard.gif" alt="Wizard" height="96"><br><b>Wizard</b><br><sub><code>WebSearch</code>, <code>WebFetch</code></sub><br><sub><a href="assets/svg-animations/clawd-working-wizard.svg"><code>clawd-working-wizard.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-conducting.gif" alt="Conducting" height="96"><br><b>Conducting</b><br><sub><code>Agent</code> / active subagents</sub><br><sub><a href="assets/svg-animations/clawd-working-conducting.svg"><code>clawd-working-conducting.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-beacon.gif" alt="Beacon" height="96"><br><b>Beacon</b><br><sub><code>LSP</code> and MCP tools (<code>mcp__*</code>)</sub><br><sub><a href="assets/svg-animations/clawd-working-beacon.svg"><code>clawd-working-beacon.svg</code></a></sub></td>
  </tr>
  <tr>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-juggling.gif" alt="Juggling" height="96"><br><b>Juggling</b><br><sub>2 working sessions on v1 (single-sprite) devices</sub><br><sub><a href="assets/svg-animations/clawd-working-juggling.svg"><code>clawd-working-juggling.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-confused.gif" alt="Confused" height="96"><br><b>Confused</b><br><sub>Waiting 60s+ for input, or a tool call failed</sub><br><sub><a href="assets/svg-animations/clawd-working-confused.svg"><code>clawd-working-confused.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-dizzy.gif" alt="Dizzy" height="96"><br><b>Dizzy</b><br><sub>API error (<code>StopFailure</code>) · triple red LED flash</sub><br><sub><a href="assets/svg-animations/clawd-dizzy.svg"><code>clawd-dizzy.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-sweeping.gif" alt="Sweeping" height="96"><br><b>Sweeping</b><br><sub>Context compaction (<code>PreCompact</code>, oneshot)</sub><br><sub><a href="assets/svg-animations/clawd-working-sweeping.svg"><code>clawd-working-sweeping.svg</code></a></sub></td>
  </tr>
  <tr>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-crab-walking.gif" alt="Walking" height="96"><br><b>Walking</b><br><sub>New session walks in; sessions reposition after one leaves</sub><br><sub><a href="assets/svg-animations/clawd-crab-walking.svg"><code>clawd-crab-walking.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-going-away.gif" alt="Going Away" height="96"><br><b>Going Away</b><br><sub>Session ended, Clawd burrows away (oneshot)</sub><br><sub><a href="assets/svg-animations/clawd-going-away.svg"><code>clawd-going-away.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-mini-clawd.gif" alt="Mini Clawd" height="96"><br><b>Mini Clawd</b><br><sub>HUD subagent counter icon</sub><br><sub><a href="assets/svg-animations/clawd-mini-clawd.svg"><code>clawd-mini-clawd.svg</code></a></sub></td>
  </tr>
</table>

### Extra animations (13)

Drawn but not used by the firmware yet.

<table>
  <tr>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-coffee-head-flip.gif" alt="Coffee Head Flip" height="96"><br><b>Coffee Head Flip</b><br><sub>Flips a coffee cup onto its head</sub><br><sub><a href="assets/svg-animations/clawd-coffee-head-flip.svg"><code>clawd-coffee-head-flip.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-eureka.gif" alt="Eureka" height="96"><br><b>Eureka</b><br><sub>Lightbulb moment</sub><br><sub><a href="assets/svg-animations/clawd-eureka.svg"><code>clawd-eureka.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-grooving.gif" alt="Grooving" height="96"><br><b>Grooving</b><br><sub>Vibing with headphones</sub><br><sub><a href="assets/svg-animations/clawd-grooving.svg"><code>clawd-grooving.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-hat-mishap.gif" alt="Hat Mishap" height="96"><br><b>Hat Mishap</b><br><sub>Wizard hat slips over its eyes</sub><br><sub><a href="assets/svg-animations/clawd-hat-mishap.svg"><code>clawd-hat-mishap.svg</code></a></sub></td>
  </tr>
  <tr>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-idle-low-battery.gif" alt="Low Battery" height="96"><br><b>Low Battery</b><br><sub>Drowsy, battery running out</sub><br><sub><a href="assets/svg-animations/clawd-idle-low-battery.svg"><code>clawd-idle-low-battery.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-wake.gif" alt="Wake" height="96"><br><b>Wake</b><br><sub>Waking up</sub><br><sub><a href="assets/svg-animations/clawd-wake.svg"><code>clawd-wake.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-builder.gif" alt="Builder" height="96"><br><b>Builder</b><br><sub>Stacking blocks</sub><br><sub><a href="assets/svg-animations/clawd-working-builder.svg"><code>clawd-working-builder.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-carrying.gif" alt="Carrying" height="96"><br><b>Carrying</b><br><sub>Hauling a box overhead</sub><br><sub><a href="assets/svg-animations/clawd-working-carrying.svg"><code>clawd-working-carrying.svg</code></a></sub></td>
  </tr>
  <tr>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-overheated.gif" alt="Overheated" height="96"><br><b>Overheated</b><br><sub>Splooted and smoking</sub><br><sub><a href="assets/svg-animations/clawd-working-overheated.svg"><code>clawd-working-overheated.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-pushing.gif" alt="Pushing" height="96"><br><b>Pushing</b><br><sub>Pushing a heavy block</sub><br><sub><a href="assets/svg-animations/clawd-working-pushing.svg"><code>clawd-working-pushing.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-working-ultra-thinking.gif" alt="Ultrathink" height="96"><br><b>Ultrathink</b><br><sub>Thinking really hard</sub><br><sub><a href="assets/svg-animations/clawd-working-ultra-thinking.svg"><code>clawd-working-ultra-thinking.svg</code></a></sub></td>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/mini-crab-typing.gif" alt="Mini Crab Typing" height="96"><br><b>Mini Crab Typing</b><br><sub>Tiny crab typing away</sub><br><sub><a href="assets/svg-animations/mini-crab-typing.svg"><code>mini-crab-typing.svg</code></a></sub></td>
  </tr>
  <tr>
    <td align="center" valign="top" width="25%"><img src="assets/slack-emojis/clawd-static-base.gif" alt="Static Base" height="96"><br><b>Static Base</b><br><sub>The base pose every animation starts from</sub><br><sub><a href="assets/svg-animations/clawd-static-base.svg"><code>clawd-static-base.svg</code></a></sub></td>
  </tr>
</table>

## Tests

```bash
# C unit tests (notification store)
cd firmware/test && make test

# Python tests (host daemon + protocol)
cd host && pip install -r requirements-dev.txt && pytest
```

## Sprite Pipeline

Clawd's animations are pixel art generated as animated SVGs, rendered to PNG frame sequences, and converted to RLE-compressed RGB565 C headers:

```bash
# SVG animation → PNG frames (requires Playwright)
python tools/svg2frames.py assets/svg-animations/clawd-working-thinking.svg /tmp/frames/ \
  --fps 8 --duration auto --scale 4

# PNG frames → C header
python tools/png2rgb565.py /tmp/frames/ firmware/main/assets/sprite_thinking.h --name thinking

# Record seamlessly-looping GIFs of all animations (for docs)
python tools/record_gif.py --all assets/captures/

# Transparent GIFs of every SVG (README gallery + Slack emoji)
python tools/svg2slack_emoji.py

# Telegram video custom emoji (100x100 WebM/VP9, ≤3s, ≤64KB; needs ffmpeg)
python tools/svg2telegram_emoji.py
TELEGRAM_BOT_TOKEN=... python tools/telegram_emoji_pack.py   # creates the pack
```

The Telegram pack is at [t.me/addemoji/clawd_tank_by_clawdtanktestbot](https://t.me/addemoji/clawd_tank_by_clawdtanktestbot) (using custom emoji needs Telegram Premium).

## BLE Debugging

```bash
# Interactive BLE tool — connect, send notifications, read config
python tools/ble_interactive.py
```

## License

MIT
