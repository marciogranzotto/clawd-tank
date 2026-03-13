# Menubar Simulator Toggle — Design Spec

## Goal

Add a checkable menu item to the macOS status bar app that enables/disables the simulator TCP transport at runtime, with per-transport connection status display and persisted preference.

## Context

The daemon supports multiple transports (BLE + simulator), but the menubar app currently only uses BLE. The simulator transport is only available via CLI flags (`--sim`, `--sim-only`). This feature exposes the simulator toggle in the GUI.

## Design

### 1. Daemon Dynamic Transport API

Two new async methods on `ClawdDaemon`:

#### `add_transport(name, client)`

```python
async def add_transport(self, name: str, client: TransportClient) -> None:
```

- Stores `client` in `self._transports[name]`
- Creates `asyncio.Queue()` in `self._transport_queues[name]`
- Enqueues current active notifications to the new queue (replay)
- Creates and stores a sender task in `self._sender_tasks[name]`

Requires storing sender tasks in a new `self._sender_tasks: dict[str, asyncio.Task]` dict. The existing `run()` method should also store tasks it creates in this dict (currently they are gathered anonymously).

#### `remove_transport(name)`

```python
async def remove_transport(self, name: str) -> None:
```

- Cancels the sender task and awaits clean cancellation (catch `CancelledError`)
- Disconnects the client if connected
- Removes entries from `_transports`, `_transport_queues`, and `_sender_tasks`

The `_transport_sender` coroutine uses `asyncio.Queue.get()` which raises `CancelledError` when the task is cancelled — no additional handling needed in the sender.

### 2. Per-Transport Status

#### Observer callback change

```python
def on_connection_change(self, connected: bool, transport: str = "") -> None:
```

The `transport` parameter defaults to `""` for backward compatibility. The daemon's `_on_transport_connect(name)` and `_on_transport_disconnect(name)` pass the transport name through.

#### Menubar status tracking

```python
self._transport_status: dict[str, bool] = {}
```

Updated in `on_connection_change`. Used to render per-transport status lines.

#### Status display

Replace the single status/subtitle pair with per-transport lines:

```
BLE: Connected
Simulator: Disconnected
```

Display names: `"ble"` → `"BLE"`, `"sim"` → `"Simulator"`.

#### Icon logic

Unchanged: connected if `any(self._transport_status.values())`, notification icon if count > 0, disconnected icon if no transports connected.

### 3. Menu Layout

```
BLE: Connected
Simulator: Disconnected
──────────────────────
Brightness [slider]
──────────────────────
Sleep Timeout        >
──────────────────────
Enable Simulator   ✓
──────────────────────
Launch at Login
──────────────────────
Reconnect
──────────────────────
Quit Clawd Tank
```

### 4. Toggle Behavior

**"Enable Simulator" menu item** — checkable, placed after Sleep Timeout.

- **Check (enable):** Creates `SimClient(port=19872)`, calls `daemon.add_transport("sim", client)` via `run_coroutine_threadsafe`. Sets menu item state to checked.
- **Uncheck (disable):** Calls `daemon.remove_transport("sim")` via `run_coroutine_threadsafe`. Sets menu item state to unchecked. Removes `"sim"` from `_transport_status`.

### 5. Persistence

Preference stored in `~/.config/clawd-tank/preferences.json`:

```json
{"sim_enabled": true}
```

- **Load:** On app init, read the file. If missing or malformed, default to `sim_enabled=false`.
- **Save:** On toggle, write the file. Create parent directory if needed.
- **Startup:** If `sim_enabled` is true, after the daemon's event loop is ready (`_loop_ready` fires), add the sim transport via `run_coroutine_threadsafe(daemon.add_transport(...))`.

### 6. Shutdown

`remove_transport` handles cleanup (cancel sender, disconnect client). The existing `_shutdown()` method should also cancel all sender tasks in `_sender_tasks` to ensure clean exit when the sim transport is active.

## Port

Fixed at `19872` (`SIM_DEFAULT_PORT`). Not configurable from the menu.

## Files Changed

- `host/clawd_tank_daemon/daemon.py` — `add_transport`, `remove_transport`, `_sender_tasks` dict, updated `run()`, updated observer calls with transport name, updated `_shutdown`
- `host/clawd_tank_menubar/app.py` — Toggle item, per-transport status display, preference load/save, startup sim init
- `host/tests/test_daemon.py` — Tests for `add_transport`, `remove_transport`
- `host/tests/test_menubar.py` — Tests for toggle state, preference persistence, per-transport observer updates

## Not In Scope

- Configurable port from the menu
- Simulator auto-discovery
- Multiple simultaneous simulator connections
