# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clawd Tank is a physical notification display for Claude Code sessions. It runs on a **Waveshare ESP32-C6-LCD-1.47** (320x172 ST7789 SPI display) and shows an animated pixel-art crab ("Clawd") alongside notification cards received over BLE from a Python host daemon.

Three components: **firmware** (ESP-IDF C), **simulator** (native macOS), **host** (Python daemon + Claude Code hooks).

## Build Commands

### Firmware (ESP-IDF 5.3.2)

Environment is managed via direnv (`firmware/.envrc`). ESP-IDF path: `bsp/esp-idf/`.

```bash
# Build
cd firmware && idf.py build

# Flash + monitor
cd firmware && idf.py -p /dev/ttyACM0 flash monitor

# Clean
cd firmware && idf.py fullclean
```

**Do not use the bond-firmware MCP plugin** — use `idf.py` directly.

### Simulator

Requires CMake 3.16+ and SDL2 (`brew install sdl2`) for local development. For distribution, use static linking (no external dependencies).

```bash
# Build (dynamic SDL2 — local development)
cd simulator && cmake -B build && cmake --build build

# Build (static SDL2 — self-contained binary for distribution)
cd simulator && cmake -B build-static -DSTATIC_SDL2=ON && cmake --build build-static

# Run interactive (SDL2 window, 1x scale, borderless, resizable)
./simulator/build/clawd-tank-sim

# Run interactive, always on top
./simulator/build/clawd-tank-sim --pinned

# Run interactive with TCP listener (daemon can connect)
./simulator/build/clawd-tank-sim --listen

# Run with window border (for development)
./simulator/build/clawd-tank-sim --bordered

# Run hidden, waiting for show_window TCP command (menu bar app mode)
./simulator/build/clawd-tank-sim --listen --hidden

# Run headless with events
./simulator/build/clawd-tank-sim --headless \
  --events 'connect; wait 500; notify "clawd-tank" "Waiting for input"; wait 2000; disconnect' \
  --screenshot-dir ./shots/ --screenshot-on-event

# Run headless with TCP listener (indefinite, daemon-driven)
./simulator/build/clawd-tank-sim --headless --listen
```

Interactive keys: `c`=connect, `d`=disconnect, `n`=notify, `1-8`=dismiss, `x`=clear, `s`=screenshot, `z`=sleep, `q`=quit.

The `--listen` flag starts a TCP server on port 19872 (configurable: `--listen 12345`). The daemon connects via `--sim` or `--sim-only` flags, or via the Simulator submenu in the menu bar app.

The window is borderless and resizable by default — drag from center, resize from edges. Integer scaling preserves pixel-crisp rendering. The simulator does not show in the Dock (`SDL_HINT_MAC_BACKGROUND_APP`).

**TCP window commands** (JSON over the `--listen` connection):
- `{"action":"show_window"}` — show the SDL window
- `{"action":"hide_window"}` — hide it (process stays alive)
- `{"action":"set_window","pinned":true}` — toggle always-on-top
- `{"action":"query_state"}` — returns JSON with current slot state, animations, and positions (debug)

**Outbound events** (simulator → client):
- `{"event":"window_hidden"}` — sent when the user closes the window (Cmd+W / X button)

### Menu Bar App (.app bundle)

```bash
# Build everything (simulator + py2app + bundle) and install to /Applications
cd host && ./build.sh --install

# Build without installing
cd host && ./build.sh
```

The build script always rebuilds the static simulator, runs `py2app`, and copies the sim binary into the `.app` bundle.

### Tests

**Important:** Firmware and host use separate Python environments. Never install host dependencies (`bleak`, `rumps`, etc.) into the ESP-IDF venv or vice versa.

```bash
# C unit tests (notification store + config store)
cd firmware/test && make test

# Python tests (host daemon) — use the host venv
cd host && .venv/bin/pytest -v

# Single Python test
cd host && .venv/bin/pytest tests/test_protocol.py -v

# If host venv needs setup:
cd host && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

### Sprite Pipeline

```bash
# Render animated SVG to PNG frame sequence
python tools/svg2frames.py <input.svg> <output_dir/> --fps 8 --duration auto --scale 4

# Convert PNG frames to RLE-compressed RGB565 C header
python tools/png2rgb565.py <input_dir> <output.h> --name <sprite_name>

# Auto-crop all sprite headers (symmetric horizontal, free vertical)
python tools/crop_sprites.py            # crops in-place
python tools/crop_sprites.py --dry-run  # preview only

# Analyze sprite bounding boxes (no changes)
python tools/analyze_sprite_bounds.py
```

### BLE Debugging

```bash
# Interactive BLE tool — connect, send notifications, read/write config
python tools/ble_interactive.py
```

## Architecture

### Data Flow

```
Claude Code hooks (SessionStart/PreToolUse/PreCompact/Stop/Notification/UserPromptSubmit/SessionEnd/SubagentStart/SubagentStop)
    → ~/.clawd-tank/clawd-tank-notify → Unix socket → clawd_tank_daemon → BLE → ESP32-C6 firmware
                                                                        ↘ TCP → Simulator (SDL2)
    Session state tracking (daemon):
        dict[session_id → state] → _compute_display_state()
            v2 transports: → set_sessions action (per-session anims + UUIDs) → multi-slot rendering
            v1 transports: → set_status action (single aggregated animation)
        Persisted to ~/.clawd-tank/sessions.json on structural changes (survives daemon restart)

    Notification cards (daemon):
        add/dismiss events → _active_notifications → add/dismiss actions → device cards
```

### Firmware (`firmware/main/`)

- **main.c** — Entry point. Creates FreeRTOS event queue, inits display/BLE, spawns `ui_task`.
- **ble_service.c** — NimBLE GATT server. Parses JSON payloads (`add`/`dismiss`/`clear`/`set_time`/`set_status`/`set_sessions` actions), posts `ble_evt_t` to queue. Handles time sync and timezone from host. Exposes a protocol version GATT characteristic (v2) for capability negotiation.
- **ui_manager.c** — State machine coordinator. Bridges BLE events to scene and notification UI. Handles `set_status` for working animations with backlight control for sleep/wake. Time display, RGB LED flash, LVGL tick.
- **scene.c** — Clawd sprite animation engine. 14 animations (IDLE, ALERT, HAPPY, SLEEPING, DISCONNECTED, THINKING, TYPING, JUGGLING, BUILDING, CONFUSED, SWEEPING, GOING_AWAY, WALKING, MINI_CLAWD). Multi-slot rendering with `MAX_VISIBLE=4` display slots and `MAX_SLOTS=6` on firmware / `MAX_SLOTS=8` on simulator (extra slots for departing animations). Walk-in animation for new sessions, going-away burrowing animation for session exits with deferred repositioning. HUD overlay with pixel-art bitmap font for subagent counter and session overflow badge. Fallback animation mechanism for oneshot return. Manages sky/stars/grass background and scene width transitions (107px with notifications, 320px idle). Firmware uses RGB565A8 pixel format (3 bytes/pixel) for frame buffers; simulator uses ARGB8888 (4 bytes/pixel). Debug introspection via `scene_get_state_json()` for `query_state` TCP action.
- **notification_ui.c** — LVGL card rendering. Auto-rotating featured card + compact list. 8-color accent palette.
- **notification.c** — Ring buffer store (max 8 notifications). Tracks by 48-char ID + sequence counter.
- **rgb_led.c** — WS2812B driver for onboard RGB LED (GPIO8). Non-blocking flash with linear fade-out via esp_timer.
- **display.c** — SPI bus + ST7789 + LVGL + PWM backlight init.
- **assets/** — RLE-compressed RGB565 sprite headers generated by `tools/png2rgb565.py`. Sprites are auto-cropped to tight bounding boxes with symmetric horizontal padding (keeps Clawd centered).
- **pixel_font.c/h** — Pixel-art bitmap font renderer for HUD overlays (subagent counter, overflow badge).

### Simulator (`simulator/`)

Compiles the **same firmware source files** unmodified. ESP-IDF APIs are replaced by shim headers in `simulator/shims/`. Uses SDL2 for display and stb_image_write for PNG capture. Supports inline event strings, JSON scenario files (`scenarios/`), a TCP listener (`--listen [port]`), always-on-top mode (`--pinned`), and window show/hide via TCP commands.

The simulator binary ships inside the Menu Bar `.app` bundle for hardware-free use. It can also be built standalone with `STATIC_SDL2=ON` for a self-contained binary (no Homebrew SDL2 needed).

Key simulator-specific files:
- **sim_ble_parse.c/h** — Shared JSON parser for TCP bridge (mirrors firmware's `parse_notification_json`). Returns 0 (BLE event), 1 (set_time), 2 (config), 3 (window command), or -1 (error).
- **sim_socket.c/h** — TCP listener with mutex-guarded ring buffers for BLE events and window commands (background pthread, main thread drains). Supports outbound events via `sim_socket_send_event()`. Handles `query_state` action returning JSON with slot state, animations, and positions.

### Host (`host/`)

- **clawd-tank-notify** — Standalone hook handler (installed to `~/.clawd-tank/clawd-tank-notify` by the menu bar app). Reads Claude Code hook stdin, converts to daemon message, forwards via Unix socket. Uses only stdlib — no external imports.
- **clawd_tank_daemon/** — Async Python daemon (asyncio). Multi-transport architecture with `TransportClient` Protocol. Supports BLE (`ClawdBleClient`) and TCP simulator (`SimClient`) transports with independent per-transport queues and sender tasks. Dynamic transport add/remove at runtime. Per-transport protocol versioning (v1 `set_status` for legacy, v2 `set_sessions` with per-session animations and UUIDs). BLE client reads the protocol version GATT characteristic on connect. Session state tracking with priority-based display state computation, staleness eviction, subagent lifecycle tracking, and per-session sweeping for v2 transports. `SimProcessManager` manages the simulator subprocess lifecycle (spawn, window commands, stdout/stderr logging through Python logger, SIGKILL on quit). `session_store.py` handles atomic save/load of session state to `~/.clawd-tank/sessions.json` for restart recovery.
- **clawd_tank_menubar/** — macOS status bar app (rumps). Transport submenus (BLE/Simulator) with independent enable/disable, connection status with colored emoji indicators, simulator window controls (show/hide, always-on-top), brightness/session timeout config, Claude Code hook installer (`hooks.py`), version display, log file output (`~/Library/Logs/ClawdTank/clawd-tank.log`). Preferences persisted to `~/.clawd-tank/preferences.json` with read-modify-write pattern. Auto-updates hooks on startup when the installed version is outdated (no manual "Install Hooks" click required). Periodic health check timer detects daemon thread crashes and shows a disconnected icon; daemon thread exceptions are caught and logged instead of dying silently.

### Session State Model

The daemon tracks per-session state and computes display state sent to the device. Protocol v2 transports receive per-session animations; v1 transports receive a single aggregated animation.

- **Per-session states**: `registered` → `thinking` → `working` → `idle` → `confused`
- **Protocol v2 (multi-session)**: Each session gets its own animation and stable UUID. Up to `MAX_VISIBLE=4` sessions shown simultaneously with individual Clawd sprites. Per-session sweeping sends sweep animation only to the compacting session.
- **Protocol v1 (legacy)**: Display states (priority order): `working_N` (1-3 sessions) > `thinking` > `confused` > `idle` > `sleeping`. Intensity tiers: 1 session = Typing, 2 = Juggling, 3+ = Building.
- **Session transitions**: New sessions walk in from offscreen. Exiting sessions play a burrowing animation, then remaining sessions reposition with walk animations. Extra sessions beyond `MAX_VISIBLE` are shown as a "+N" overflow badge.
- **Special events**: `PreCompact` → per-session sweeping (v2) or global oneshot sweep (v1), `Notification` (idle_prompt) → confused
- **Staleness eviction**: Sessions with no events within the configurable timeout (default 10min) are evicted. No sessions = sleeping.
- **Subagent tracking**: `SubagentStart`/`SubagentStop` hooks track active `agent_id`s per session. Sessions with active subagents are never evicted and count as "working" in display state. HUD overlay shows mini-crab icon with subagent count.
- **Session persistence**: Session state is saved atomically to `~/.clawd-tank/sessions.json` on structural state changes. Daemon loads saved state on startup with immediate stale session eviction, so restarting the menu bar app preserves the correct display state for running Claude Code sessions.

## Key Constraints

- **Display**: 320x172 pixels, 16-bit RGB565, SPI. All UI must fit this resolution.
- **Target chip**: ESP32-C6FH8 (RISC-V, single core). 8MB flash, no PSRAM. ~512 KB internal SRAM (~200 KB free heap after IDF/BLE/LVGL).
- **BLE MTU**: 256 bytes. Notification JSON payloads must stay under this limit.
- **Notification limit**: 8 simultaneous (ring buffer, oldest dropped on overflow).
- **LVGL version**: 9.5.0 (not 8.x — API differs significantly).
- **Sprite format**: RLE-compressed RGB565 arrays with transparency key color `0x18C5`. Auto-cropped with symmetric horizontal padding. Firmware decodes to RGB565A8 (3 bytes/pixel); simulator decodes to ARGB8888 (4 bytes/pixel). One frame buffer per active slot, lazy-allocated.
- **RGB LED**: Onboard WS2812B on GPIO8, driven via `espressif/led_strip` component. Flashes on notifications.
- **Time sync**: No WiFi/NTP. Host daemon sends epoch + POSIX timezone string over BLE on each connect (`set_time` action).

## TODO Tracking

Always check `TODO.md` at the start of a session to understand current project status. After completing any work, update `TODO.md` to reflect what was done — check off finished items, add progress notes to partial items, and add new items as they are discovered.
