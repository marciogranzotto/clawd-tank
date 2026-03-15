# Clawd Notification Display — TODO

## Status (v1.2.1)

Firmware builds, flashes, and runs on the Waveshare ESP32-C6-LCD-1.47 board.
BLE advertising works, notifications can be sent and dismissed via BLE GATT writes.
23 C tests pass (with ASan+UBSan), 152 Python tests pass (11 test files).
Clawd sprite animations and notification card UI are implemented.
NVS-backed config store supports brightness and session timeout with BLE read/write.
macOS menu bar app provides daemon control, device configuration UI, and simulator toggle.
Daemon supports multi-transport (BLE + TCP simulator) with dynamic add/remove at runtime.
Simulator supports TCP listener (`--listen`) for daemon-driven operation without hardware.
Session-aware working animations driven by Claude Code hooks with intensity tiers.
11 animated sprites integrated into scene.c (5 original + 6 working animations).
Session state tracking in daemon with priority-based display state computation.
Staleness eviction replaces timer-based sleep — sleep is now session-driven.
Subagent lifecycle tracking prevents sleeping during long-running agent tasks.
Session state persisted to disk — restarting the app preserves display state.
Build script (`host/build.sh`) automates simulator + py2app + bundle.
Auto-update hooks, daemon health monitoring, orphan sim cleanup, launchd auto-migration.

---

## Working Animations (v1.1.0) — Complete

- [x] **Session state tracking in daemon** — `dict[session_id → state]` with `_compute_display_state()` priority resolution. States: registered → thinking → working → idle → confused. Display priority: working_N > thinking > confused > idle > sleeping.
- [x] **Intensity tiers** — 1 session working = Typing animation, 2 sessions = Juggling, 3+ sessions = Building.
- [x] **Hook protocol expansion** — New hooks: SessionStart, PreToolUse, PreCompact. Hook discriminator field on existing add/dismiss events for daemon-side session state differentiation.
- [x] **6 new sprite animations** — thinking (32f), typing (12f), juggling (10f), building (8f), confused (48f), sweeping (12f, oneshot). All 180×180px @ 8fps. Generated from SVG sources via svg2frames.py + png2rgb565.py pipeline.
- [x] **Fallback animation mechanism** — `scene_set_fallback_anim()` allows oneshots (alert, happy, sweeping) to return to the current working animation instead of always IDLE.
- [x] **set_status BLE/TCP action** — `display_status_t` enum with string-to-enum mapping in both firmware and simulator JSON parsers.
- [x] **Session-driven sleep model** — Timer-based sleep removed from firmware. Sleep is now daemon-driven: no sessions = sleeping. Staleness eviction (configurable timeout, default 10min) handles ungraceful session termination.
- [x] **PreCompact sweeping** — Daemon sends sweeping oneshot followed by computed state as fallback.
- [x] **Hook migration detection** — `are_hooks_installed()` checks all required hooks, not just any. Install button always updates to latest.
- [x] **Menu bar "Session Timeout"** — Renamed from "Sleep Timeout", wired to daemon staleness timeout.

## Sprites & Animations (v1.0.0) — Complete

- [x] **All 5 SVG animations converted to C sprite headers** — idle (96f/135×135), alert (40f/135×135), happy (20f/120×120), sleeping (36f/120×120), disconnected (36f/150×120). Frame buffer cap raised from 48→96. All `#if HAS_*_SPRITE` guards removed from `scene.c`. Alert and happy remain non-looping (one-shot). Idle, sleeping, disconnected loop.

## UI/UX Design (v1.0.0) — Complete

- [x] **Notification entry animation** — fade animation implemented (300ms ease-out via `lv_anim` + `lv_obj_set_style_opa`). Fade-in on show, fade-out with hide callback on dismiss. Instant path preserved for disconnect/clear.
- [x] **Transition animation** — full-screen new notification → compact list view. New notification triggers a 2.5s hero/expanded card view (fills the notification panel), then animates via `lv_anim` with `lv_anim_path_ease_in_out` over 350ms down to the compact list height. Implemented in `notification_ui.c` via `notification_ui_trigger_hero()` (called from `ui_manager.c` on `BLE_EVT_NOTIF_ADD`). Auto-rotation and repeat-hero on rapid new notifications are both handled correctly.
- [x] **Text truncation and scrolling** — featured card uses `LV_LABEL_LONG_SCROLL_CIRCULAR` marquee for both project name and message. Compact list uses `LV_LABEL_LONG_DOT` ("..."). Manual `snprintf` truncation removed.

## Simulator Improvements (v1.1.0) — Complete

- [x] **Default scale changed to 2x** — Window opens at 640×344 instead of 960×516.
- [x] **`--pinned` flag** — Always-on-top mode via `SDL_SetWindowAlwaysOnTop`.
- [x] **Auto-focus on launch** — `SDL_RaiseWindow` brings window to front.
- [x] **Shutdown freeze fix** — Client socket closed during shutdown to unblock `recv()` in listener thread.

## Python Host Hardening (v1.0.0) — Complete

- [x] **Socket length framing** (`socket_server.py`) — switched to newline-delimited messages; server uses `readline()`, sender appends `\n`
- [x] **`sys.exit(1)` in hook** (`clawd-notify`) — changed to `sys.exit(0)` with explanatory comment; notifications are best-effort
- [x] **Log file context manager** (`clawd-notify`) — `open()` now in a `with` block; handle closed even if `Popen` raises
- [x] **Broad `except Exception`** (`socket_server.py`) — `JSONDecodeError` caught separately with `logger.error`, `TimeoutError` caught explicitly with `logger.warning`, remaining unexpected errors use `logger.exception()` for full traceback

## Testing Improvements (v1.0.0) — Complete

- [x] **Add sanitizers to C test Makefile** — `-fsanitize=address,undefined -Werror` added to CFLAGS and LDFLAGS; 18/18 tests pass clean
- [x] **Test `_replay_active`** — 4 tests: sends all active, empty store, skips unknown events, concurrent mutation safe
- [x] **Test BLE write failure → reconnect → replay path** — 2 tests: single and multi-notification replay after write failure
- [x] **Test `cwd=""`** (empty string explicitly) — verified `Path("").name` triggers the `"unknown"` fallback

## Simulator-Daemon Bridge (v1.0.0) — Complete

- [x] **TCP socket listener in simulator** — background pthread with mutex-guarded ring buffer queue, newline-delimited JSON protocol matching BLE GATT format. `--listen [port]` CLI flag (default 19872). Shared JSON parser (`sim_ble_parse.c`) mirrors firmware's `parse_notification_json`.
- [x] **Multi-transport daemon architecture** — `TransportClient` Protocol, `SimClient` TCP transport, per-transport queues and sender tasks. `--sim` / `--sim-only` CLI flags. Dynamic `add_transport`/`remove_transport` methods.
- [x] **Per-transport observer status** — `on_connection_change` includes transport name. Menubar shows per-transport status lines (BLE/Simulator: Connected/Connecting...).
- [x] **Simulator toggle in menubar** — "Enable Simulator" checkable menu item with preference persistence (`~/.clawd-tank/preferences.json`). Dynamically adds/removes sim transport at runtime.
- [x] **Initial replay on connect** — `_transport_sender` replays active notifications after initial connect, so dynamically-added transports show existing notifications.
- [x] **Stop hook support** — `protocol.py` handles Stop hook event to show "Waiting for input" notification immediately when Claude stops.

## Code Quality (v1.0.0) — Complete

- [x] **Document `_lock_t` locking intent** (`ui_manager.c`) — comment explaining the lock covers both `rebuild_ui()` and `lv_timer_handler()`
- [x] **Comment `display_init()` return** (`main.c:42`) — return value intentionally discarded; LVGL tracks default display internally
- [x] **LVGL mutex migration** — added TODO comment in `ui_manager.c` noting the `lv_lock()`/`lv_unlock()` migration consideration and flush-ready integration concern

## Subagent Tracking (v1.2.0) — Complete

- [x] **SubagentStart/SubagentStop hooks** — New hooks registered and forwarded to daemon via the hook handler script.
- [x] **Per-session subagent tracking** — `subagents: set[agent_id]` tracked per session in daemon state dict.
- [x] **Eviction suppression** — Sessions with active subagents are never evicted by staleness checker.
- [x] **Display state integration** — Sessions with active subagents count as "working" in display state computation, preventing Clawd from sleeping during long subagent tasks.

## Session State Persistence (v1.2.0) — Complete

- [x] **Atomic session state save/load** — `save_sessions()`/`load_sessions()` in `session_store.py` serialize session state dict to `~/.clawd-tank/sessions.json` with set↔list conversion. Atomic writes via temp file + `os.replace`.
- [x] **Smart persistence** — Session state saved only on structural changes (state transitions, subagent add/remove), not on every `last_event` timestamp update. Reduces disk writes during heavy tool use.
- [x] **Daemon startup recovery** — Loads saved sessions on init with immediate staleness eviction. Restarting the menu bar app immediately shows correct animation for running Claude Code sessions.

## Daemon Resilience (v1.2.1) — Complete

- [x] **Auto-update hooks on startup** — Hooks are checked and updated automatically on app launch when outdated, removing the need for manual "Install Hooks" clicks after code updates.
- [x] **Daemon thread crash logging** — Daemon thread exceptions are caught and logged instead of dying silently. Periodic health check timer detects dead daemon and shows disconnected icon.
- [x] **Orphaned sim process cleanup** — On startup, orphaned simulator processes on the listen port are identified by name and killed instead of being connected to.
- [x] **Display state sync on replay** — `_last_display_state` is updated after transport replay to prevent duplicate broadcasts.

## Future Considerations (Out of Scope)

- Physical button interaction (dismiss notifications from the device)
- Multiple host device support (pairing with more than one Mac)
- OTA firmware updates over WiFi
- Per-session project name display during working animations
