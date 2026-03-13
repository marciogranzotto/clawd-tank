# Simulator-Daemon Bridge Design

**Date:** 2026-03-13
**Status:** Approved

## Problem

The simulator and daemon are disconnected. The simulator only accepts events from CLI arguments or keypresses, while the daemon communicates exclusively over BLE to real hardware. There is no way to test the full Claude Code hooks → daemon → display pipeline without physical ESP32 hardware.

## Solution

Add a TCP socket bridge between the simulator and daemon, so the daemon can drive the simulator using the same protocol it uses for BLE. Both transports (BLE and TCP) run simultaneously.

## Architecture

```
Claude Code hooks → clawd-tank-notify → Unix socket → daemon
                                                        ├── BLE → ESP32 (existing)
                                                        └── TCP:19872 → simulator (new)
```

## Simulator: TCP Socket Listener

### New module: `sim_socket.c` / `sim_socket.h`

Activated with `--listen [port]` (default port: `19872`).

- Spawns a **background pthread** that binds, listens, and accepts one TCP client at a time
- On TCP client connect → calls `ui_manager_handle_event()` with `BLE_EVT_CONNECTED`
- On TCP client disconnect → calls `ui_manager_handle_event()` with `BLE_EVT_DISCONNECTED`
- Reads **newline-delimited JSON** from the TCP socket, same format as the firmware's BLE notification characteristic

### Notification protocol (same as BLE GATT writes)

```json
{"action": "add", "id": "<session_id>", "project": "<name>", "message": "<text>"}
{"action": "dismiss", "id": "<session_id>"}
{"action": "clear"}
{"action": "set_time", "epoch": 1741825200, "tz": "UTC-3"}
```

Parsed using the same logic as `parse_notification_json()` in `ble_service.c`. The simulator reimplements this parser in `sim_socket.c` (the firmware version depends on ESP-IDF APIs like `xQueueSend`; the simulator calls `ui_manager_handle_event()` directly).

### Config protocol (new, TCP-only)

**Write config** (fire-and-forget, daemon → simulator):
```json
{"action": "write_config", "brightness": 128, "sleep_timeout": 300}
```
Simulator applies via `config_store_set_brightness()`, `config_store_set_sleep_timeout()`, and `ui_manager_set_sleep_timeout()`.

**Read config** (request/response):
- Daemon sends: `{"action": "read_config"}\n`
- Simulator responds: `{"brightness": 128, "sleep_timeout": 300}\n`

### Integration with existing simulator

- Coexists with keyboard input, `--events`, `--scenario` — all event sources feed `ui_manager_handle_event()`
- Works in both interactive and headless modes
- The main loop is unchanged; socket events arrive from the background thread

## Daemon: Simulator Transport

### New module: `sim_client.py`

A `SimClient` class that implements the same interface as `ClawdBleClient`:

| Method | Behavior |
|---|---|
| `connect()` | TCP connect to `localhost:19872`, retries on connection refused |
| `disconnect()` | Close TCP socket |
| `is_connected` | Returns TCP socket connected state |
| `write_notification(payload)` | Sends `payload + "\n"` over TCP |
| `read_config()` | Sends `{"action": "read_config"}\n`, reads response line, returns parsed dict |
| `write_config(payload)` | Sends `payload + "\n"` over TCP |

Disconnect detection: TCP EOF or socket error triggers the same reconnect/replay logic as BLE.

### Simultaneous transports

The daemon runs **both** BLE and TCP transports when `--sim` is passed:

- `ClawdDaemon` holds a list of transport clients instead of a single `_ble` client
- `_ble_sender` broadcasts each payload to all **connected** transports
- Each transport connects/disconnects independently
- `_sync_time` and `_replay_active` run per-transport on connect
- Observer is notified if any transport connects or all transports disconnect

### Daemon CLI

```
clawd-tank-daemon --sim              # BLE + TCP on default port 19872
clawd-tank-daemon --sim-port 12345   # BLE + TCP on custom port
```

`--sim` enables TCP transport alongside BLE. Without `--sim`, behavior is unchanged (BLE only).

## Usage

```bash
# Terminal 1: simulator with TCP listener
./simulator/build/clawd-tank-sim --listen

# Terminal 2: daemon with both transports
clawd-tank-daemon --sim

# Terminal 3: send notification (unchanged)
echo '{"event":"add","session_id":"test-1","project":"my-app","message":"Waiting"}' | clawd-tank-notify
```

Both the real ESP32 and the simulator window show the same notifications.

## File Changes

### New files
- `simulator/sim_socket.c` — TCP listener thread, JSON parser, event dispatch
- `simulator/sim_socket.h` — Public API (`sim_socket_init`, `sim_socket_shutdown`)
- `host/clawd_tank_daemon/sim_client.py` — TCP transport client

### Modified files
- `simulator/sim_main.c` — Add `--listen` CLI option, call `sim_socket_init()` / `sim_socket_shutdown()`
- `simulator/CMakeLists.txt` — Add `sim_socket.c` to sources
- `host/clawd_tank_daemon/daemon.py` — Multi-transport support, `--sim` / `--sim-port` flags
- `host/clawd_tank_daemon/__main__.py` or CLI entry point — Pass new flags through

## Testing

- **Simulator unit**: Start simulator with `--listen`, send JSON over TCP with a test script, verify events fire
- **Integration**: Run full pipeline (simulator + daemon + clawd-tank-notify), verify notifications appear
- **Simultaneous**: Run with both BLE device and simulator, verify both receive the same notifications
- **Reconnect**: Kill and restart simulator, verify daemon reconnects and replays active notifications
- **Config**: Read/write config over TCP, verify values applied in simulator
