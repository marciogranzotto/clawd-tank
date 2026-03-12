# Clawd Notification Display — TODO

## Status

Firmware builds, flashes, and runs on the Waveshare ESP32-C6-LCD-1.47 board.
BLE advertising works, notifications can be sent and dismissed via BLE GATT writes.
18 C tests pass (with ASan+UBSan), 47 Python tests pass (6 test files).
Clawd sprite animations and notification card UI are implemented.
NVS-backed config store supports brightness and sleep timeout with BLE read/write.
macOS menu bar app provides daemon control and device configuration UI.
All 5 animated SVGs converted to full sprite frame sequences and integrated into scene.c:
- idle: 96 frames, 135×135px, 6fps (frame buffer cap raised from 48→96)
- alert: 40 frames, 135×135px, 10fps
- happy: 20 frames, 120×120px, 10fps
- sleeping: 36 frames, 120×120px, 6fps
- disconnected: 36 frames, 150×120px, 6fps
All HAS_*_SPRITE conditional guards removed; sprites included unconditionally.

---

## Sprites & Animations (Major)

- [x] **All 5 SVG animations converted to C sprite headers** — idle (96f/135×135), alert (40f/135×135), happy (20f/120×120), sleeping (36f/120×120), disconnected (36f/150×120). Frame buffer cap raised from 48→96. All `#if HAS_*_SPRITE` guards removed from `scene.c`. Alert and happy remain non-looping (one-shot). Idle, sleeping, disconnected loop.

## UI/UX Design (Major)

- [x] **Notification entry animation** — fade animation implemented (300ms ease-out via `lv_anim` + `lv_obj_set_style_opa`). Fade-in on show, fade-out with hide callback on dismiss. Instant path preserved for disconnect/clear.
- [x] **Transition animation** — full-screen new notification → compact list view. New notification triggers a 2.5s hero/expanded card view (fills the notification panel), then animates via `lv_anim` with `lv_anim_path_ease_in_out` over 350ms down to the compact list height. Implemented in `notification_ui.c` via `notification_ui_trigger_hero()` (called from `ui_manager.c` on `BLE_EVT_NOTIF_ADD`). Auto-rotation and repeat-hero on rapid new notifications are both handled correctly.
- [x] **Text truncation and scrolling** — featured card uses `LV_LABEL_LONG_SCROLL_CIRCULAR` marquee for both project name and message. Compact list uses `LV_LABEL_LONG_DOT` ("..."). Manual `snprintf` truncation removed.

## Python Host Hardening (Medium Priority)

- [x] **Socket length framing** (`socket_server.py`) — switched to newline-delimited messages; server uses `readline()`, sender appends `\n`
- [x] **`sys.exit(1)` in hook** (`clawd-notify`) — changed to `sys.exit(0)` with explanatory comment; notifications are best-effort
- [x] **Log file context manager** (`clawd-notify`) — `open()` now in a `with` block; handle closed even if `Popen` raises
- [x] **Broad `except Exception`** (`socket_server.py`) — `JSONDecodeError` caught separately with `logger.error`, `TimeoutError` caught explicitly with `logger.warning`, remaining unexpected errors use `logger.exception()` for full traceback

## Testing Improvements (Low Priority)

- [x] **Add sanitizers to C test Makefile** — `-fsanitize=address,undefined -Werror` added to CFLAGS and LDFLAGS; 18/18 tests pass clean
- [x] **Test `_replay_active`** — 4 tests: sends all active, empty store, skips unknown events, concurrent mutation safe
- [x] **Test BLE write failure → reconnect → replay path** — 2 tests: single and multi-notification replay after write failure
- [x] **Test `cwd=""`** (empty string explicitly) — verified `Path("").name` triggers the `"unknown"` fallback

## Code Quality (Low Priority)

- [x] **Document `_lock_t` locking intent** (`ui_manager.c`) — comment explaining the lock covers both `rebuild_ui()` and `lv_timer_handler()`
- [x] **Comment `display_init()` return** (`main.c:42`) — return value intentionally discarded; LVGL tracks default display internally
- [x] **LVGL mutex migration** — added TODO comment in `ui_manager.c` noting the `lv_lock()`/`lv_unlock()` migration consideration and flush-ready integration concern

## Future Considerations (Out of Scope)

- Physical button interaction (dismiss notifications from the device)
- Multiple host device support (pairing with more than one Mac)
- OTA firmware updates over WiFi
