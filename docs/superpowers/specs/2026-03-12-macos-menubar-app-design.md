# macOS Menu Bar App for Clawd Tank

## Summary

A macOS status bar application that wraps the existing Clawd Tank daemon, providing a GUI for device configuration (brightness, sleep timeout) and status monitoring (connection state, notification count). The daemon code becomes a reusable library with two entry points: the existing headless CLI and the new menu bar app.

## Goals

- Single-click launch experience for Clawd Tank on macOS
- Configure device settings (brightness, sleep timeout) from the menu bar
- At-a-glance connection and notification status via the status bar icon
- Preserve headless daemon mode for users who don't want the GUI
- Persist device settings across reboots via NVS on the ESP32

## Non-Goals

- Windows/Linux support (macOS only, using rumps/PyObjC)
- Rich notification UI in the menu (notifications are displayed on the device, not the Mac)
- OTA firmware updates through the menu bar app

## Architecture

### Hybrid Embed with Headless Fallback

The daemon code (`clawd_tank_daemon/`) is a library that can run either embedded in the status bar app or standalone via CLI. Both entry points use the same `ClawdDaemon` class.

```
┌─────────────────────┐      ┌─────────────────────┐
│  clawd-tank-menubar │  OR  │  clawd-tank-daemon   │
│  (rumps + asyncio)  │      │  (headless CLI)      │
│                     │      │                      │
│  embeds daemon lib  │      │  same daemon lib     │
└────────┬────────────┘      └────────┬─────────────┘
         │                            │
         ▼                            ▼
┌──────────────────────────────────────────────────┐
│              ClawdDaemon Library                  │
│  SocketServer ←── hooks    ClawdBleClient ──► BLE│
└──────────────────────────┬───────────────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │  ESP32-C6    │
                    │  BLE GATT    │
                    │  notif char  │
                    │  config char │
                    └──────────────┘
```

The existing file lock (`~/.clawd-tank/daemon.lock`) prevents both entry points from running simultaneously.

### Threading Model

The menu bar app uses `rumps`, which requires the PyObjC/NSApplication main loop on the main thread. The daemon's asyncio event loop runs in a daemon thread. Communication between the two happens via thread-safe callbacks and `asyncio.run_coroutine_threadsafe()`.

## BLE Protocol Extension

### New Config Characteristic

A second GATT characteristic is added under the existing Clawd Tank service for device configuration.

- **UUID**: `E9F6E626-5FCA-4201-B80C-4D2B51C40F51`
- **Flags**: `READ | WRITE | WRITE_NO_RSP`
- **Max attribute size**: 512 bytes

#### Write Format

Partial JSON — only include fields to change:

```json
{"brightness": 128}
```

```json
{"sleep_timeout": 600}
```

```json
{"brightness": 200, "sleep_timeout": 120}
```

#### Read Format

Always returns the full current configuration:

```json
{"brightness": 102, "sleep_timeout": 300}
```

#### Long Read/Write Support

The characteristic supports BLE Long Read (Read Blob) and Long Write (Prepare Write + Execute Write) for payloads exceeding ATT_MTU.

**Read (firmware side):** NimBLE handles Long Read fragmentation internally. The access callback is called once per read — it appends the full value to `ctxt->om`, and the stack slices the response at the correct ATT offset for Read Blob requests automatically.

```c
static int config_read_cb(uint16_t conn_handle, uint16_t attr_handle,
                           struct ble_gatt_access_ctxt *ctxt, void *arg) {
    if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
        char buf[512];
        uint16_t len = config_store_serialize_json(buf, sizeof(buf));
        int rc = os_mbuf_append(ctxt->om, buf, len);
        return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    // ... write handling
}
```

**Write (firmware side):** For writes, NimBLE's Prepare Write queue assembles the full payload before invoking the access callback, so the write callback receives the complete JSON regardless of MTU. No special offset handling needed on writes.

**Client side (bleak):** Bleak handles Long Read and Long Write transparently — `read_gatt_char()` and `write_gatt_char()` work without changes regardless of payload size.

This ensures the protocol works correctly regardless of negotiated MTU size, and accommodates future config fields without requiring application-level fragmentation.

### Config Fields

| Field | Type | Range | Default | Description |
|-------|------|-------|---------|-------------|
| `brightness` | int | 0-255 | 102 (~40%) | Display backlight PWM duty cycle |
| `sleep_timeout` | int | 0-3600 | 300 (5 min) | Seconds idle before sleep. 0 = never |

### NVS Persistence

- Config is stored in NVS namespace `clawd_cfg` with keys `brightness` (u8) and `sleep_timeout` (u16)
- Loaded on boot with defaults if keys don't exist
- Written to NVS on each config write from BLE
- Applied immediately: brightness updates LEDC duty, sleep timeout updates the `ui_manager` threshold

## Firmware Changes

### New Files

- **`config_store.c/.h`** — NVS read/write operations, in-memory `device_config_t` struct, getter/setter functions. Public API:
  - `void config_store_init(void)` — load from NVS (or defaults)
  - `uint8_t config_store_get_brightness(void)` — current brightness value
  - `uint32_t config_store_get_sleep_timeout_ms(void)` — current sleep timeout in ms
  - `void config_store_set_brightness(uint8_t duty)` — update + NVS persist
  - `void config_store_set_sleep_timeout(uint16_t seconds)` — update + NVS persist
  - `uint16_t config_store_serialize_json(char *buf, uint16_t buf_sz)` — serialize full config to JSON, returns length

### Modified Files

- **`ble_service.c`** — Add config characteristic to the GATT service definition. Read callback uses `config_store_serialize_json()` with offset handling (see Long Read/Write section). Write callback parses partial JSON, calls `config_store_set_*` + `display_set_brightness()` / `ui_manager_set_sleep_timeout()` to apply immediately
- **`display.c`** — Export `void display_set_brightness(uint8_t duty)` which calls `ledc_set_duty()` + `ledc_update_duty()` on `LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0`. Initial brightness loaded from `config_store_get_brightness()` instead of hardcoded 102. Call chain: `ble_service.c` includes `display.h` — no circular dependency since `display.h` is a leaf module with no includes beyond ESP-IDF
- **`ui_manager.c`** — Replace `SLEEP_TIMEOUT_MS` macro with a static variable initialized from `config_store_get_sleep_timeout_ms()`. Export `void ui_manager_set_sleep_timeout(uint32_t ms)` to update it at runtime, also resets `s_last_activity_tick` so the new timeout takes effect from the current moment
- **`main.c`** — Call `config_store_init()` before `display_init()` so initial brightness is available

## Host Changes

### ClawdBleClient Additions

New methods on the existing BLE client class:

```python
async def read_config(self) -> dict:
    """Read full device config from the config characteristic."""

async def write_config(self, payload: str) -> bool:
    """Write a partial config JSON to the config characteristic."""
```

### ClawdDaemon Observer Interface

The daemon class gets a callback interface so the menu bar app can react to state changes:

```python
class DaemonObserver(Protocol):
    def on_connection_change(self, connected: bool) -> None: ...
    def on_notification_change(self, count: int) -> None: ...
```

`ClawdDaemon.__init__` accepts an optional `observer: DaemonObserver`. Call sites:

- **`on_connection_change(True)`** — called in `_ble_sender` after `ensure_connected()` succeeds (first connect or reconnect). Also requires a new `_on_ble_disconnect` callback registered with `ClawdBleClient` that calls `on_connection_change(False)`.
- **`on_notification_change(count)`** — called at the end of `_handle_message` after updating `_active_notifications`, with `len(self._active_notifications)` as the count.

### New Package: `host/clawd_tank_menubar/`

- **`app.py`** — `rumps.App` subclass implementing `DaemonObserver`
  - Creates `ClawdDaemon` with itself as observer
  - Runs asyncio loop in a daemon thread via `threading.Thread(daemon=True)`
  - Uses `asyncio.run_coroutine_threadsafe()` to call daemon methods from the main thread (e.g., sending config changes)
  - Updates menu items from observer callbacks dispatched to the main thread via `PyObjCTools.AppHelper.callAfter()` (the standard way to schedule work on the AppKit main thread from a background thread; rumps re-exports PyObjC as a dependency)

- **`icons/`** — macOS template images for status bar:
  - `crab-connected.png` — crab with green dot (connected, no notifications)
  - `crab-notifications.png` — crab with orange dot (notifications pending)
  - `crab-disconnected.png` — grayed crab (disconnected)
  - All as 16x16 and 32x32 @2x template images

### Entry Points

Defined in `setup.py` / `pyproject.toml`:

- `clawd-tank-daemon` — existing, unchanged
- `clawd-tank-menubar` — new, launches `clawd_tank_menubar.app:main`

### New Dependencies

- `rumps` — macOS status bar apps in Python (pulls in `pyobjc-framework-Cocoa` as a transitive dependency, which provides the PyObjC bridge needed for custom NSView menu items and `callAfter` main-thread dispatch)

## Menu Bar UI

### Menu Structure (Connected)

```
🦀● Clawd Tank                    Connected
   2 active notifications
─────────────────────────────────────────
   Brightness                        40%
   [━━━━━━━━░░░░░░░░░░░░]
─────────────────────────────────────────
   Sleep Timeout                  ▸ 5 min
     ├─ 1 minute
     ├─ 2 minutes
     ├─ 5 minutes  ✓
     ├─ 10 minutes
     ├─ 30 minutes
     └─ Never
─────────────────────────────────────────
   Launch at Login                    ✓
─────────────────────────────────────────
   Reconnect
─────────────────────────────────────────
   Quit Clawd Tank
```

### Menu Structure (Disconnected)

- Status shows "Disconnected" with gray dot
- "Scanning for device..." subtitle
- Brightness and Sleep Timeout items grayed out (values show "--")
- Reconnect grayed out
- Launch at Login and Quit remain active

### Status Bar Icon States

| State | Icon | Indicator |
|-------|------|-----------|
| Connected, no notifications | Crab | Green dot |
| Connected, notifications pending | Crab | Orange dot |
| Disconnected | Grayed crab | None |

### Brightness Slider

Implemented as a custom `NSSlider` inside an `NSView` embedded in an `NSMenuItem` via PyObjC. Rumps does not have a built-in slider menu item, so we create one using PyObjC directly: create an `NSView` containing an `NSSlider`, set it as the menu item's `view` property. The slider's target/action calls back into the app to send BLE config writes, debounced with a timestamp check (send at most every 200ms, flush final value on mouse-up).

### Launch at Login

Implemented via a `launchd` user agent plist:
- `~/Library/LaunchAgents/com.clawd-tank.menubar.plist`
- Toggle writes/removes the plist and uses `launchctl bootstrap`/`bootout` (the modern API on macOS 10.15+) to load/unload the agent

## Testing

### Firmware

- Unit test `config_store` — NVS mock for read/write/defaults
- Unit test config characteristic JSON parsing — partial updates, invalid fields, full read response

### Host

- Test `ClawdBleClient.read_config` / `write_config` — mock bleak
- Test `DaemonObserver` callbacks — verify connection and notification change events fire
- Test menu bar app state transitions — connected/disconnected/notification changes update icon and menu items (mock rumps)

### Integration

- Simulator: extend BLE shim in `simulator/shims/` to support the config characteristic. Add a simulated config store (in-memory `device_config_t` with JSON read/write handlers) and expose simulated brightness changes as log output. This allows `--events` to include config commands (e.g., `config '{"brightness":200}'`) for automated testing.
- Manual: verify brightness slider updates display in real-time, sleep timeout change takes effect, NVS persistence across reboot

### Error Handling

- `write_config` returns `bool`. On failure, the menu bar app logs the error and shows a brief "Config write failed" status in the menu subtitle. No retry — the user can adjust the slider again. On disconnect during config write, the normal reconnect flow handles recovery; config is re-read on reconnect to sync the UI.
