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

- **UUID**: new 128-bit UUID (to be generated at implementation time)
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

The characteristic supports BLE Long Read (Read Blob) and Long Write (Prepare Write + Execute Write) for payloads exceeding ATT_MTU. NimBLE handles this at the GATT layer via the `offset` parameter in the access callback. Bleak handles it transparently on the client side.

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

- **`config_store.c/.h`** — NVS read/write operations, in-memory `device_config_t` struct, getter/setter functions used by `display.c` (brightness) and `ui_manager.c` (sleep timeout)

### Modified Files

- **`ble_service.c`** — Add config characteristic to the GATT service definition. New read callback (serialize config to JSON, handle offset for Long Read) and write callback (parse partial JSON, update config store, apply settings)
- **`display.c`** — Export a `display_set_brightness(uint8_t duty)` function. Initial brightness loaded from config store instead of hardcoded
- **`ui_manager.c`** — Sleep timeout loaded from config store instead of hardcoded `SLEEP_TIMEOUT_MS`. Expose a function to update it at runtime
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

`ClawdDaemon.__init__` accepts an optional `observer: DaemonObserver`. The daemon calls these methods when BLE connection state changes or the active notification count changes.

### New Package: `host/clawd_tank_menubar/`

- **`app.py`** — `rumps.App` subclass implementing `DaemonObserver`
  - Creates `ClawdDaemon` with itself as observer
  - Runs asyncio loop in a daemon thread via `threading.Thread(daemon=True)`
  - Uses `asyncio.run_coroutine_threadsafe()` to call daemon methods from the main thread (e.g., sending config changes)
  - Updates menu items from observer callbacks (dispatched to main thread via `rumps.Timer` or `PyObjC` performSelectorOnMainThread)

- **`icons/`** — macOS template images for status bar:
  - `crab-connected.png` — crab with green dot (connected, no notifications)
  - `crab-notifications.png` — crab with orange dot (notifications pending)
  - `crab-disconnected.png` — grayed crab (disconnected)
  - All as 16x16 and 32x32 @2x template images

### Entry Points

Defined in `setup.py` / `pyproject.toml`:

- `clawd-tank-daemon` — existing, unchanged
- `clawd-tank-menubar` — new, launches `clawd_tank_menubar.app:main`

### New Dependency

- `rumps` — macOS status bar apps in Python

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

Implemented as a custom `NSView` embedded in an `NSMenuItem`. Rumps supports this via `rumps.SliderMenuItem` or a custom view wrapper. The slider sends config writes as the user drags, debounced to avoid flooding BLE (e.g., send at most every 200ms).

### Launch at Login

Implemented via a `launchd` user agent plist:
- `~/Library/LaunchAgents/com.clawd-tank.menubar.plist`
- Toggle writes/removes the plist and loads/unloads with `launchctl`

## Testing

### Firmware

- Unit test `config_store` — NVS mock for read/write/defaults
- Unit test config characteristic JSON parsing — partial updates, invalid fields, full read response

### Host

- Test `ClawdBleClient.read_config` / `write_config` — mock bleak
- Test `DaemonObserver` callbacks — verify connection and notification change events fire
- Test menu bar app state transitions — connected/disconnected/notification changes update icon and menu items (mock rumps)

### Integration

- Simulator: extend BLE shim to support the config characteristic for end-to-end testing
- Manual: verify brightness slider updates display in real-time, sleep timeout change takes effect, NVS persistence across reboot
