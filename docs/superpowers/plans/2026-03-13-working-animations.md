# Working Animations Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session-aware working animations to Clawd Tank, driven by Claude Code hooks, with intensity tiers based on concurrent session count.

**Architecture:** The daemon tracks per-session state and computes a single display state sent to the device via a new `set_status` BLE/TCP action. The firmware renders the corresponding animation. Session states flow: `registered → thinking → working → idle → confused`. Display priority: `working_N > thinking > confused > idle > sleeping`.

**Tech Stack:** Python 3 (asyncio), C (ESP-IDF/FreeRTOS), LVGL 9.5, NimBLE, cJSON

**Spec:** `docs/superpowers/specs/2026-03-13-working-animations-design.md`

---

## Chunk 1: Host Protocol & Hooks

### Task 1: Update `protocol.py` — new hook events and `hook` discriminator

**Files:**
- Modify: `host/clawd_tank_daemon/protocol.py`
- Test: `host/tests/test_protocol.py`

- [ ] **Step 1: Write failing tests for new hook events**

Add to `host/tests/test_protocol.py`:

```python
def test_session_start_produces_session_start_event():
    hook = {
        "hook_event_name": "SessionStart",
        "session_id": "sess-001",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "session_start"
    assert msg["session_id"] == "sess-001"


def test_pre_tool_use_produces_tool_use_event():
    hook = {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-001",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "tool_use"
    assert msg["session_id"] == "sess-001"


def test_pre_compact_produces_compact_event():
    hook = {
        "hook_event_name": "PreCompact",
        "session_id": "sess-001",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "compact"
    assert msg["session_id"] == "sess-001"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v -k "session_start or tool_use or compact"`
Expected: FAIL — new events return `None` (unknown hook)

- [ ] **Step 3: Write failing tests for `hook` discriminator on existing events**

Add to `host/tests/test_protocol.py`:

```python
def test_stop_add_includes_hook_discriminator():
    hook = {
        "hook_event_name": "Stop",
        "session_id": "sess-001",
        "cwd": "/tmp/proj",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg["event"] == "add"
    assert msg["hook"] == "Stop"


def test_notification_add_includes_hook_discriminator():
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "sess-001",
        "cwd": "/tmp/proj",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg["event"] == "add"
    assert msg["hook"] == "Notification"


def test_prompt_submit_dismiss_includes_hook_discriminator():
    hook = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "sess-001",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg["event"] == "dismiss"
    assert msg["hook"] == "UserPromptSubmit"


def test_session_end_dismiss_includes_hook_discriminator():
    hook = {
        "hook_event_name": "SessionEnd",
        "session_id": "sess-001",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg["event"] == "dismiss"
    assert msg["hook"] == "SessionEnd"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v -k "hook_discriminator"`
Expected: FAIL — `hook` key not present in messages

- [ ] **Step 5: Implement new events and discriminator in `protocol.py`**

In `host/clawd_tank_daemon/protocol.py`, update `hook_payload_to_daemon_message()`:

```python
def hook_payload_to_daemon_message(hook: dict) -> Optional[dict]:
    """Convert a Claude Code hook stdin payload to a daemon message.

    Returns None if the hook event is not relevant (should be ignored).
    """
    event_name = hook.get("hook_event_name", "")
    session_id = hook.get("session_id", "")

    if event_name == "SessionStart":
        return {"event": "session_start", "session_id": session_id}

    if event_name == "PreToolUse":
        return {"event": "tool_use", "session_id": session_id}

    if event_name == "PreCompact":
        return {"event": "compact", "session_id": session_id}

    if event_name == "Stop":
        cwd = hook.get("cwd", "")
        project = Path(cwd).name if cwd else "unknown"
        if not project:
            project = "unknown"
        return {
            "event": "add",
            "hook": "Stop",
            "session_id": session_id,
            "project": project,
            "message": "Waiting for input",
        }

    if event_name == "Notification":
        if hook.get("notification_type") != "idle_prompt":
            return None
        cwd = hook.get("cwd", "")
        project = Path(cwd).name if cwd else "unknown"
        if not project:
            project = "unknown"
        message = hook.get("message", "Waiting for input")
        return {
            "event": "add",
            "hook": "Notification",
            "session_id": session_id,
            "project": project,
            "message": message,
        }

    if event_name == "UserPromptSubmit":
        return {
            "event": "dismiss",
            "hook": "UserPromptSubmit",
            "session_id": session_id,
        }

    if event_name == "SessionEnd":
        return {
            "event": "dismiss",
            "hook": "SessionEnd",
            "session_id": session_id,
        }

    return None
```

- [ ] **Step 6: Update `daemon_message_to_ble_payload` to handle new event types gracefully**

Session-state events (`session_start`, `tool_use`, `compact`) are daemon-internal and do NOT produce BLE payloads. Update `daemon_message_to_ble_payload()` to return `None` instead of raising for these:

```python
def daemon_message_to_ble_payload(msg: dict) -> Optional[str]:
    """Convert a daemon message to a JSON string for BLE GATT write.

    Returns None for daemon-internal events that don't produce BLE payloads.
    """
    event = msg["event"]

    if event == "add":
        return json.dumps({
            "action": "add",
            "id": msg.get("session_id", ""),
            "project": msg.get("project", ""),
            "message": msg.get("message", ""),
        })

    if event == "dismiss":
        return json.dumps({
            "action": "dismiss",
            "id": msg.get("session_id", ""),
        })

    if event == "clear":
        return json.dumps({"action": "clear"})

    if event in ("session_start", "tool_use", "compact"):
        return None  # daemon-internal, no BLE payload

    raise ValueError(f"Unknown event: {event}")
```

- [ ] **Step 7: Fix existing test for return type change**

The test `test_ble_payload_unknown_event_raises` still expects `ValueError` for truly unknown events — that's fine. But `test_daemon_add_to_ble` and `test_daemon_dismiss_to_ble` should still pass since `add`/`dismiss` still return strings. Add a new test:

```python
def test_session_events_produce_no_ble_payload():
    """Session-state events are daemon-internal and must not produce BLE payloads."""
    assert daemon_message_to_ble_payload({"event": "session_start", "session_id": "s1"}) is None
    assert daemon_message_to_ble_payload({"event": "tool_use", "session_id": "s1"}) is None
    assert daemon_message_to_ble_payload({"event": "compact", "session_id": "s1"}) is None
```

- [ ] **Step 8: Run all protocol tests**

Run: `cd host && .venv/bin/pytest tests/test_protocol.py -v`
Expected: ALL PASS

- [ ] **Step 9: Update daemon `_transport_sender` to handle `None` payload**

In `host/clawd_tank_daemon/daemon.py`, update `_transport_sender()` at line 194-198. Change the `ValueError` catch to also handle `None` returns:

```python
            try:
                payload = daemon_message_to_ble_payload(msg)
            except ValueError:
                logger.error("[%s] Skipping unknown event: %s", name, msg.get("event"))
                continue
            if payload is None:
                continue  # daemon-internal event, no BLE payload
```

- [ ] **Step 10: Run all existing tests to verify no regressions**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 11: Commit**

```bash
git add host/clawd_tank_daemon/protocol.py host/clawd_tank_daemon/daemon.py host/tests/test_protocol.py
git commit -m "feat(host): add session state events and hook discriminator to protocol"
```

### Task 2: Update hooks registration and standalone notify script

**Files:**
- Modify: `host/clawd_tank_menubar/hooks.py`

- [ ] **Step 1: Add new hooks to `HOOKS_CONFIG`**

In `host/clawd_tank_menubar/hooks.py`, replace `HOOKS_CONFIG` (lines 97-113) with:

```python
HOOKS_CONFIG = {
    "SessionStart": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
    "Stop": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
    "Notification": [
        {
            "matcher": "idle_prompt",
            "hooks": [{"type": "command", "command": HOOK_COMMAND}],
        }
    ],
    "UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
    "PreToolUse": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
    "PreCompact": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
    "SessionEnd": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
}
```

- [ ] **Step 2: Update standalone `NOTIFY_SCRIPT` `hook_to_message()` function**

In `host/clawd_tank_menubar/hooks.py`, update the `hook_to_message()` function inside `NOTIFY_SCRIPT` (lines 34-64) to match the new `protocol.py` logic:

```python
    def hook_to_message(hook):
        """Convert a Claude Code hook payload to a daemon message."""
        event_name = hook.get("hook_event_name", "")
        session_id = hook.get("session_id", "")

        if event_name == "SessionStart":
            return {"event": "session_start", "session_id": session_id}

        if event_name == "PreToolUse":
            return {"event": "tool_use", "session_id": session_id}

        if event_name == "PreCompact":
            return {"event": "compact", "session_id": session_id}

        if event_name == "Stop":
            cwd = hook.get("cwd", "")
            project = Path(cwd).name if cwd else "unknown"
            return {
                "event": "add",
                "hook": "Stop",
                "session_id": session_id,
                "project": project or "unknown",
                "message": "Waiting for input",
            }

        if event_name == "Notification":
            if hook.get("notification_type") != "idle_prompt":
                return None
            cwd = hook.get("cwd", "")
            project = Path(cwd).name if cwd else "unknown"
            return {
                "event": "add",
                "hook": "Notification",
                "session_id": session_id,
                "project": project or "unknown",
                "message": hook.get("message", "Waiting for input"),
            }

        if event_name == "UserPromptSubmit":
            return {
                "event": "dismiss",
                "hook": "UserPromptSubmit",
                "session_id": session_id,
            }

        if event_name == "SessionEnd":
            return {
                "event": "dismiss",
                "hook": "SessionEnd",
                "session_id": session_id,
            }

        return None
```

- [ ] **Step 3: Run all tests**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add host/clawd_tank_menubar/hooks.py
git commit -m "feat(hooks): register SessionStart, PreToolUse, PreCompact hooks and update standalone script"
```

## Chunk 2: Daemon Session State Tracker

### Task 3: Add session state tracking to daemon

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py`
- Create: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests for `_compute_display_state()`**

Create `host/tests/test_session_state.py`:

```python
"""Tests for daemon session state tracking and display state computation."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from clawd_tank_daemon.daemon import ClawdDaemon


def make_daemon():
    """Create a daemon in sim-only mode with no actual transport."""
    d = ClawdDaemon(sim_only=True)
    # Remove the default transports so we can test state tracking in isolation
    d._transports.clear()
    d._transport_queues.clear()
    return d


# --- Priority resolution ---

def test_no_sessions_returns_sleeping():
    d = make_daemon()
    assert d._compute_display_state() == "sleeping"


def test_single_registered_session_returns_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "registered", "last_event": time.time()}
    assert d._compute_display_state() == "idle"


def test_single_idle_session_returns_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    assert d._compute_display_state() == "idle"


def test_single_thinking_session_returns_thinking():
    d = make_daemon()
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    assert d._compute_display_state() == "thinking"


def test_single_working_session_returns_working_1():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    assert d._compute_display_state() == "working_1"


def test_two_working_sessions_returns_working_2():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    d._session_states["s2"] = {"state": "working", "last_event": time.time()}
    assert d._compute_display_state() == "working_2"


def test_three_plus_working_sessions_returns_working_3():
    d = make_daemon()
    for i in range(5):
        d._session_states[f"s{i}"] = {"state": "working", "last_event": time.time()}
    assert d._compute_display_state() == "working_3"


def test_working_beats_thinking():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    d._session_states["s2"] = {"state": "thinking", "last_event": time.time()}
    assert d._compute_display_state() == "working_1"


def test_thinking_beats_confused():
    d = make_daemon()
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    d._session_states["s2"] = {"state": "confused", "last_event": time.time()}
    assert d._compute_display_state() == "thinking"


def test_confused_beats_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "confused", "last_event": time.time()}
    d._session_states["s2"] = {"state": "idle", "last_event": time.time()}
    assert d._compute_display_state() == "confused"


def test_registered_treated_as_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "registered", "last_event": time.time()}
    d._session_states["s2"] = {"state": "confused", "last_event": time.time()}
    assert d._compute_display_state() == "confused"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`
Expected: FAIL — `_compute_display_state` and `_session_states` don't exist

- [ ] **Step 3: Implement session state tracking in daemon**

In `host/clawd_tank_daemon/daemon.py`, add to `__init__` (after line 116):

```python
        self._session_states: dict[str, dict] = {}  # {session_id: {"state": str, "last_event": float}}
        self._last_display_state: str = "sleeping"
```

Add `_compute_display_state` method to `ClawdDaemon`:

```python
    def _compute_display_state(self) -> str:
        """Compute the display state from all session states.

        Priority: working_N > thinking > confused > idle > sleeping.
        """
        if not self._session_states:
            return "sleeping"

        working_count = sum(
            1 for s in self._session_states.values() if s["state"] == "working"
        )
        if working_count > 0:
            return f"working_{min(working_count, 3)}"

        if any(s["state"] == "thinking" for s in self._session_states.values()):
            return "thinking"

        if any(s["state"] == "confused" for s in self._session_states.values()):
            return "confused"

        # All sessions are idle or registered
        return "idle"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat(daemon): add session state tracking and display state computation"
```

### Task 4: Wire session state updates into `_handle_message`

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py`
- Modify: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests for session state transitions via `_handle_message`**

Add to `host/tests/test_session_state.py`:

```python
@pytest.mark.asyncio
async def test_session_start_registers_session():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert "s1" in d._session_states
    assert d._session_states["s1"]["state"] == "registered"


@pytest.mark.asyncio
async def test_prompt_submit_sets_thinking():
    """A dismiss with hook=UserPromptSubmit sets session to thinking."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "thinking"


@pytest.mark.asyncio
async def test_tool_use_sets_working():
    d = make_daemon()
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "working"


@pytest.mark.asyncio
async def test_stop_add_sets_idle():
    """An add with hook=Stop sets session to idle."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"


@pytest.mark.asyncio
async def test_notification_add_sets_confused():
    """An add with hook=Notification sets session to confused."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "Notification", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "confused"


@pytest.mark.asyncio
async def test_session_end_removes_session():
    """A dismiss with hook=SessionEnd removes the session."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert "s1" not in d._session_states


@pytest.mark.asyncio
async def test_implicit_session_creation():
    """Events for unknown sessions create them implicitly."""
    d = make_daemon()
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert "s1" in d._session_states
    assert d._session_states["s1"]["state"] == "working"


@pytest.mark.asyncio
async def test_last_display_state_tracks_changes():
    """_last_display_state updates as sessions change state."""
    d = make_daemon()
    assert d._last_display_state == "sleeping"
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert d._last_display_state == "idle"
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._last_display_state == "thinking"
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._last_display_state == "working_1"
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._last_display_state == "idle"
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert d._last_display_state == "sleeping"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "handle_message or registers or sets_ or removes or implicit"`
Expected: FAIL

- [ ] **Step 3: Update `_handle_message` with session state logic**

In `host/clawd_tank_daemon/daemon.py`, update `_handle_message()` (currently lines 123-139):

```python
    async def _handle_message(self, msg: dict) -> None:
        """Handle a message from clawd-tank-notify via the socket."""
        event = msg.get("event")
        session_id = msg.get("session_id", "")
        hook = msg.get("hook", "")
        logger.info("Socket msg: event=%s hook=%s session=%s project=%s",
                     event, hook, session_id[:12], msg.get("project", "?"))

        # --- Notification card management (unchanged) ---
        if event == "add":
            self._active_notifications[session_id] = msg
        elif event == "dismiss":
            self._active_notifications.pop(session_id, None)

        # --- Session state tracking ---
        self._update_session_state(event, hook, session_id)

        # --- Broadcast to transports ---
        for q in self._transport_queues.values():
            await q.put(msg)

        if self._observer:
            self._observer.on_notification_change(len(self._active_notifications))

        # --- Check if display state changed (skip for compact, handled above) ---
        if event != "compact":
            await self._broadcast_display_state_if_changed()
```

Add `_update_session_state` method:

```python
    def _update_session_state(self, event: str, hook: str, session_id: str) -> None:
        """Update session state based on incoming event."""
        if not session_id:
            return

        now = time.time()

        if event == "session_start":
            self._session_states[session_id] = {"state": "registered", "last_event": now}
        elif event == "tool_use":
            self._session_states.setdefault(session_id, {"state": "working", "last_event": now})
            self._session_states[session_id]["state"] = "working"
            self._session_states[session_id]["last_event"] = now
        elif event == "compact":
            if session_id in self._session_states:
                self._session_states[session_id]["last_event"] = now
        elif event == "add":
            self._session_states.setdefault(session_id, {"state": "idle", "last_event": now})
            if hook == "Stop":
                self._session_states[session_id]["state"] = "idle"
            elif hook == "Notification":
                self._session_states[session_id]["state"] = "confused"
            self._session_states[session_id]["last_event"] = now
        elif event == "dismiss":
            if hook == "SessionEnd":
                self._session_states.pop(session_id, None)
            elif hook == "UserPromptSubmit":
                self._session_states.setdefault(session_id, {"state": "thinking", "last_event": now})
                self._session_states[session_id]["state"] = "thinking"
                self._session_states[session_id]["last_event"] = now
            else:
                # Legacy dismiss without hook — just update timestamp
                if session_id in self._session_states:
                    self._session_states[session_id]["last_event"] = now
```

Add `_broadcast_display_state_if_changed` method:

```python
    async def _broadcast_display_state_if_changed(self) -> None:
        """Compute display state and broadcast set_status if it changed."""
        new_state = self._compute_display_state()
        if new_state == self._last_display_state:
            return
        self._last_display_state = new_state
        payload = json.dumps({"action": "set_status", "status": new_state})
        for transport in self._transports.values():
            if transport.is_connected:
                await transport.write_notification(payload)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run all tests to verify no regressions**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat(daemon): wire session state updates into message handler with display broadcasting"
```

### Task 5: Add staleness eviction and compact handling

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py`
- Modify: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests for staleness eviction**

Add to `host/tests/test_session_state.py`:

```python
def test_staleness_evicts_old_sessions():
    d = make_daemon()
    # Add a session with a timestamp far in the past
    d._session_states["s1"] = {"state": "idle", "last_event": time.time() - 9999}
    d._session_staleness_timeout = 1  # 1 second for test
    d._evict_stale_sessions()
    assert "s1" not in d._session_states


def test_staleness_keeps_fresh_sessions():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    d._session_staleness_timeout = 600
    d._evict_stale_sessions()
    assert "s1" in d._session_states


@pytest.mark.asyncio
async def test_compact_triggers_sweeping():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    # Add a mock transport to capture payloads
    transport = AsyncMock()
    transport.is_connected = True
    d._transports["test"] = transport
    d._transport_queues["test"] = asyncio.Queue()
    d._last_display_state = "working_1"

    await d._handle_message({"event": "compact", "session_id": "s1"})

    # Should have sent sweeping then the computed state
    calls = transport.write_notification.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    assert any(p.get("status") == "sweeping" for p in payloads)
    assert any(p.get("status") == "working_1" for p in payloads)
```

(Add `import json` to the test file imports if not already present.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v -k "staleness or compact"`
Expected: FAIL

- [ ] **Step 3: Implement staleness eviction**

In `host/clawd_tank_daemon/daemon.py`, add to `__init__`:

```python
        self._session_staleness_timeout: float = 600.0  # 10 minutes default
```

Add method:

```python
    def _evict_stale_sessions(self) -> None:
        """Remove sessions that haven't sent events within the timeout."""
        now = time.time()
        stale = [
            sid for sid, s in self._session_states.items()
            if now - s["last_event"] > self._session_staleness_timeout
        ]
        for sid in stale:
            logger.info("Evicting stale session: %s", sid[:12])
            del self._session_states[sid]
```

- [ ] **Step 4: Implement compact handling in `_handle_message`**

Update the `compact` branch in `_update_session_state` — it already updates the timestamp. Add sweeping broadcast in `_handle_message`:

After the `_update_session_state` call, add:

```python
        # --- Handle compact: send sweeping oneshot ---
        if event == "compact":
            sweeping_payload = json.dumps({"action": "set_status", "status": "sweeping"})
            for transport in self._transports.values():
                if transport.is_connected:
                    await transport.write_notification(sweeping_payload)
            # Send computed state immediately after (firmware uses as fallback)
            computed = self._compute_display_state()
            fallback_payload = json.dumps({"action": "set_status", "status": computed})
            for transport in self._transports.values():
                if transport.is_connected:
                    await transport.write_notification(fallback_payload)
            self._last_display_state = computed
```

- [ ] **Step 5: Add periodic staleness check to `run()`**

In the `run()` method, add a background task that runs `_evict_stale_sessions` every 30 seconds:

```python
    async def _staleness_checker(self) -> None:
        """Periodically evict stale sessions."""
        while self._running:
            await asyncio.sleep(30)
            self._evict_stale_sessions()
            await self._broadcast_display_state_if_changed()
```

In `run()`, after starting sender tasks (after line 312):

```python
        self._staleness_task = asyncio.create_task(self._staleness_checker())
```

In `_shutdown()`, before cancelling sender tasks:

```python
        if hasattr(self, '_staleness_task'):
            self._staleness_task.cancel()
            try:
                await self._staleness_task
            except asyncio.CancelledError:
                pass
```

- [ ] **Step 6: Wire sleep_timeout config to staleness timeout**

Update `write_config` or add a `set_session_timeout` method. When the menu bar app writes a sleep_timeout config, also update `self._session_staleness_timeout`. In `_handle_message`, check if the config is being set.

Actually, the simplest approach: the menu bar app already calls `daemon.write_config()`. Add a method the menu bar can call:

```python
    def set_session_timeout(self, seconds: int) -> None:
        """Update the session staleness timeout."""
        self._session_staleness_timeout = float(seconds)
        logger.info("Session staleness timeout set to %ds", seconds)
```

- [ ] **Step 7: Run tests**

Run: `cd host && .venv/bin/pytest tests/test_session_state.py -v`
Expected: ALL PASS

- [ ] **Step 8: Run all tests**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat(daemon): add staleness eviction and PreCompact sweeping broadcast"
```

### Task 6: Update transport reconnect to send display state

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py`

- [ ] **Step 1: Update `_replay_active_for` to also send display state**

In `host/clawd_tank_daemon/daemon.py`, update `_replay_active_for()` (lines 169-178) to send the current display state after replaying notifications:

```python
    async def _replay_active_for(self, transport) -> None:
        """Replay all active notifications and current display state to a transport after reconnect."""
        logger.info("Replaying %d active notifications", len(self._active_notifications))
        for msg in list(self._active_notifications.values()):
            try:
                payload = daemon_message_to_ble_payload(msg)
            except ValueError:
                continue
            if payload is None:
                continue
            await transport.write_notification(payload)
            await asyncio.sleep(0.05)
        # Send current display state
        state = self._compute_display_state()
        status_payload = json.dumps({"action": "set_status", "status": state})
        await transport.write_notification(status_payload)
```

- [ ] **Step 2: Run all tests**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add host/clawd_tank_daemon/daemon.py
git commit -m "feat(daemon): send display state on transport reconnect"
```

## Chunk 3: Firmware Protocol Layer

### Task 7: Add `display_status_t` and `BLE_EVT_SET_STATUS` to firmware

**Files:**
- Modify: `firmware/main/ble_service.h`
- Modify: `firmware/main/ble_service.c`
- Modify: `simulator/sim_ble_parse.c`

- [ ] **Step 1: Replace enums and struct in `ble_service.h`**

In `firmware/main/ble_service.h`, **replace** the existing `ble_evt_type_t` enum (lines 10-16) and `ble_evt_t` struct (lines 18-23) with the following. Add the new `display_status_t` enum before them. Keep the existing `#ifndef` guards and `#include` lines unchanged:

```c
/* Display status values sent by daemon via set_status action */
typedef enum {
    DISPLAY_STATUS_SLEEPING,
    DISPLAY_STATUS_IDLE,
    DISPLAY_STATUS_THINKING,
    DISPLAY_STATUS_WORKING_1,
    DISPLAY_STATUS_WORKING_2,
    DISPLAY_STATUS_WORKING_3,
    DISPLAY_STATUS_CONFUSED,
    DISPLAY_STATUS_SWEEPING,
} display_status_t;

typedef enum {
    BLE_EVT_NOTIF_ADD,
    BLE_EVT_NOTIF_DISMISS,
    BLE_EVT_NOTIF_CLEAR,
    BLE_EVT_SET_STATUS,
    BLE_EVT_CONNECTED,
    BLE_EVT_DISCONNECTED,
} ble_evt_type_t;

typedef struct {
    ble_evt_type_t type;
    char id[NOTIF_MAX_ID_LEN];
    char project[NOTIF_MAX_PROJ_LEN];
    char message[NOTIF_MAX_MSG_LEN];
    uint8_t status;  /* display_status_t, used only for BLE_EVT_SET_STATUS */
} ble_evt_t;
```

- [ ] **Step 2: Add `set_status` parsing to `ble_service.c`**

In `firmware/main/ble_service.c`, add a status string-to-enum mapping function before `parse_notification_json`:

```c
static int parse_display_status(const char *str) {
    if (strcmp(str, "sleeping") == 0) return DISPLAY_STATUS_SLEEPING;
    if (strcmp(str, "idle") == 0) return DISPLAY_STATUS_IDLE;
    if (strcmp(str, "thinking") == 0) return DISPLAY_STATUS_THINKING;
    if (strcmp(str, "working_1") == 0) return DISPLAY_STATUS_WORKING_1;
    if (strcmp(str, "working_2") == 0) return DISPLAY_STATUS_WORKING_2;
    if (strcmp(str, "working_3") == 0) return DISPLAY_STATUS_WORKING_3;
    if (strcmp(str, "confused") == 0) return DISPLAY_STATUS_CONFUSED;
    if (strcmp(str, "sweeping") == 0) return DISPLAY_STATUS_SWEEPING;
    return -1;
}
```

In `parse_notification_json()`, add a new branch after the `set_time` handling (before the `else` at line 107):

```c
    } else if (strcmp(action->valuestring, "set_status") == 0) {
        cJSON *status = cJSON_GetObjectItem(json, "status");
        if (!status || !cJSON_IsString(status)) {
            ESP_LOGW(TAG, "set_status: missing 'status' field");
            cJSON_Delete(json);
            return;
        }
        int s = parse_display_status(status->valuestring);
        if (s < 0) {
            ESP_LOGW(TAG, "set_status: unknown status '%s'", status->valuestring);
            cJSON_Delete(json);
            return;
        }
        evt.type = BLE_EVT_SET_STATUS;
        evt.status = (uint8_t)s;
```

- [ ] **Step 3: Add `set_status` parsing to `sim_ble_parse.c`**

In `simulator/sim_ble_parse.c`, add the same `parse_display_status()` function. Then add a new branch in `sim_ble_parse_json()` after the `set_time` handling (before the `write_config`/`read_config` check at line 63):

```c
    } else if (strcmp(action->valuestring, "set_status") == 0) {
        cJSON *status = cJSON_GetObjectItem(json, "status");
        if (!status || !cJSON_IsString(status)) {
            cJSON_Delete(json);
            return -1;
        }
        int s = parse_display_status(status->valuestring);
        if (s < 0) {
            cJSON_Delete(json);
            return -1;
        }
        out->type = BLE_EVT_SET_STATUS;
        out->status = (uint8_t)s;
```

- [ ] **Step 4: Build firmware to verify compilation**

Run: `cd firmware && idf.py build`
Expected: BUILD SUCCESS (no new sprites yet, but the enum/struct changes and parsing should compile)

- [ ] **Step 5: Build simulator to verify compilation**

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: BUILD SUCCESS

- [ ] **Step 6: Commit**

```bash
git add firmware/main/ble_service.h firmware/main/ble_service.c simulator/sim_ble_parse.c
git commit -m "feat(firmware): add set_status BLE action with display_status_t enum"
```

## Chunk 4: Firmware Scene & UI Manager

### Task 8: Add fallback animation mechanism to scene

**Files:**
- Modify: `firmware/main/scene.h`
- Modify: `firmware/main/scene.c`

- [ ] **Step 1: Add `scene_set_fallback_anim` declaration to `scene.h`**

In `firmware/main/scene.h`, add after line 16 (`scene_set_clawd_anim`):

```c
void scene_set_fallback_anim(scene_t *scene, clawd_anim_id_t anim);
```

- [ ] **Step 2: Add `fallback_anim` field to `scene_t` struct in `scene.c`**

In `firmware/main/scene.c`, add to `struct scene_t` after `last_frame_tick` (line 141):

```c
    clawd_anim_id_t fallback_anim;  /* animation to return to after oneshot */
```

- [ ] **Step 3: Initialize `fallback_anim` in `scene_create`**

In `scene_create()`, after initializing `cur_anim` to `CLAWD_ANIM_IDLE`, add:

```c
    s->fallback_anim = CLAWD_ANIM_IDLE;
```

- [ ] **Step 4: Implement `scene_set_fallback_anim`**

Add after `scene_set_clawd_anim()`:

```c
void scene_set_fallback_anim(scene_t *scene, clawd_anim_id_t anim)
{
    if (!scene) return;
    scene->fallback_anim = anim;
}
```

- [ ] **Step 5: Change oneshot fallback in `scene_tick` to use `fallback_anim`**

In `firmware/main/scene.c`, line 375, change:

```c
                scene_set_clawd_anim(scene, CLAWD_ANIM_IDLE);
```

to:

```c
                scene_set_clawd_anim(scene, scene->fallback_anim);
```

- [ ] **Step 6: Build firmware and simulator**

Run: `cd firmware && idf.py build && cd ../simulator && cmake --build build`
Expected: BUILD SUCCESS

- [ ] **Step 7: Commit**

```bash
git add firmware/main/scene.h firmware/main/scene.c
git commit -m "feat(scene): add fallback animation for oneshot return"
```

### Task 9: Update `ui_manager.c` to handle `BLE_EVT_SET_STATUS`

**Files:**
- Modify: `firmware/main/ui_manager.c`

- [ ] **Step 1: Add display status state variable**

In `firmware/main/ui_manager.c`, add after `s_sleeping` (line 54):

```c
static display_status_t s_display_status = DISPLAY_STATUS_IDLE;
```

- [ ] **Step 2: Add status-to-animation mapping function**

Add before `transition_to()`:

```c
static clawd_anim_id_t status_to_anim(display_status_t status) {
    switch (status) {
    case DISPLAY_STATUS_SLEEPING:  return CLAWD_ANIM_SLEEPING;
    case DISPLAY_STATUS_IDLE:      return CLAWD_ANIM_IDLE;
    case DISPLAY_STATUS_THINKING:  return CLAWD_ANIM_IDLE;  /* placeholder until sprite exists */
    case DISPLAY_STATUS_WORKING_1: return CLAWD_ANIM_IDLE;  /* placeholder */
    case DISPLAY_STATUS_WORKING_2: return CLAWD_ANIM_IDLE;  /* placeholder */
    case DISPLAY_STATUS_WORKING_3: return CLAWD_ANIM_IDLE;  /* placeholder */
    case DISPLAY_STATUS_CONFUSED:  return CLAWD_ANIM_IDLE;  /* placeholder */
    case DISPLAY_STATUS_SWEEPING:  return CLAWD_ANIM_IDLE;  /* placeholder */
    default:                       return CLAWD_ANIM_IDLE;
    }
}
```

Note: These map to `CLAWD_ANIM_IDLE` as placeholders. When new sprite assets are added (Task 11), each gets its own `CLAWD_ANIM_*` entry and this mapping is updated.

- [ ] **Step 3: Update `transition_to(UI_STATE_FULL_IDLE)` to use display status**

In `firmware/main/ui_manager.c`, replace lines 96-99:

```c
        /* Don't overwrite a oneshot animation (happy/alert) */
        if (!scene_is_playing_oneshot(s_scene)) {
            scene_set_clawd_anim(s_scene, CLAWD_ANIM_IDLE);
        }
```

with:

```c
        /* Don't overwrite a oneshot animation (happy/alert) */
        if (!scene_is_playing_oneshot(s_scene)) {
            scene_set_clawd_anim(s_scene, status_to_anim(s_display_status));
        }
        scene_set_fallback_anim(s_scene, status_to_anim(s_display_status));
```

- [ ] **Step 4: Handle `BLE_EVT_SET_STATUS` in `ui_manager_handle_event`**

Add a new case in the switch at line 165, before the closing `}` at line 225:

```c
    case BLE_EVT_SET_STATUS: {
        display_status_t new_status = (display_status_t)evt->status;
        ESP_LOGI(TAG, "Set status: %d", new_status);
        display_status_t old_status = s_display_status;
        s_display_status = new_status;

        clawd_anim_id_t anim = status_to_anim(new_status);
        scene_set_fallback_anim(s_scene, anim);

        /* Handle backlight for sleep/wake */
        if (new_status == DISPLAY_STATUS_SLEEPING && old_status != DISPLAY_STATUS_SLEEPING) {
            display_set_brightness(0);
        } else if (new_status != DISPLAY_STATUS_SLEEPING && old_status == DISPLAY_STATUS_SLEEPING) {
            display_set_brightness(config_store_get_brightness());
        }

        /* Don't interrupt a playing oneshot — the fallback will take effect when it finishes */
        if (!scene_is_playing_oneshot(s_scene)) {
            scene_set_clawd_anim(s_scene, anim);
        }

        s_last_activity_tick = lv_tick_get();
        break;
    }
```

- [ ] **Step 5: Remove sleep timer logic and dead code from `ui_manager.c`**

In `firmware/main/ui_manager.c`:

1. Remove the sleep timeout check (lines 239-247):
```c
    /* Sleep timeout: 5 minutes of idle while connected */
    if (s_state == UI_STATE_FULL_IDLE && s_connected && !s_sleeping) {
        uint32_t elapsed = lv_tick_get() - s_last_activity_tick;
        if (s_sleep_timeout_ms > 0 && elapsed >= s_sleep_timeout_ms) {
            scene_set_clawd_anim(s_scene, CLAWD_ANIM_SLEEPING);
            s_sleeping = true;
            ESP_LOGI(TAG, "Clawd falling asleep (5m idle)");
        }
    }
```

2. Remove the now-unused static variables:
   - `static uint32_t s_sleep_timeout_ms = 5 * 60 * 1000;` (line 27)
   - `static bool s_sleeping = false;` (line 54)

3. Remove the now-dead `ui_manager_set_sleep_timeout()` function (lines 268-281) and its declaration from `ui_manager.h` (line 19).

4. Remove `s_sleeping = false;` assignments in `transition_to()` (lines 101, 113, 124) since the variable no longer exists.

- [ ] **Step 6: Update time display to show in full-width regardless of state**

In `firmware/main/ui_manager.c`, change line 250:

```c
    if (s_state == UI_STATE_FULL_IDLE) {
```

to:

```c
    if (s_state != UI_STATE_NOTIFICATION && s_state != UI_STATE_DISCONNECTED) {
```

Also update `transition_to(UI_STATE_FULL_IDLE)` to always show time (line 94 already has `scene_set_time_visible(s_scene, true)` — this is fine).

- [ ] **Step 7: Build firmware and simulator**

Run: `cd firmware && idf.py build && cd ../simulator && cmake --build build`
Expected: BUILD SUCCESS

- [ ] **Step 8: Commit**

```bash
git add firmware/main/ui_manager.c
git commit -m "feat(ui_manager): handle set_status events, remove sleep timer, show clock in all full-width states"
```

## Chunk 5: Menu Bar & Integration

### Task 10: Update menu bar app labels and wire session timeout

**Files:**
- Modify: `host/clawd_tank_menubar/app.py`

- [ ] **Step 1: Rename constant and menu item**

In `host/clawd_tank_menubar/app.py`:

1. Rename `SLEEP_TIMEOUT_OPTIONS` (line 22) to `SESSION_TIMEOUT_OPTIONS`.
2. Rename `self._sleep_menu` (line 58) to `self._session_timeout_menu` and change its label from `"Sleep Timeout"` to `"Session Timeout"`.
3. Rename `self._sleep_timeout_value` (line 59) to `self._session_timeout_value`.
4. Update all references: lines 60, 65, 99, 202-204, 238, 240, 244.

- [ ] **Step 2: Wire timeout changes to daemon's `set_session_timeout`**

In `_on_sleep_timeout_select` (line 236, rename to `_on_session_timeout_select`), add after the `write_config` call (line 247):

```python
            # Update daemon staleness timeout
            if self._daemon:
                self._daemon.set_session_timeout(seconds)
```

This uses `asyncio.run_coroutine_threadsafe` indirectly — `set_session_timeout` is a sync method so it can be called directly from the menu bar thread.

- [ ] **Step 3: Run all tests**

Run: `cd host && .venv/bin/pytest -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add host/clawd_tank_menubar/app.py
git commit -m "feat(menubar): rename sleep timeout to session timeout"
```

### Task 11: Add new sprite animations (when assets are ready)

This task is blocked on having PNG frame sequences for the 6 new animations. The SVG sources exist in `assets/svg-animations/` but need to be converted to PNG frames and then to RLE-compressed C headers.

**Files:**
- Create: `firmware/main/assets/sprite_thinking.h`
- Create: `firmware/main/assets/sprite_typing.h`
- Create: `firmware/main/assets/sprite_juggling.h`
- Create: `firmware/main/assets/sprite_building.h`
- Create: `firmware/main/assets/sprite_confused.h`
- Create: `firmware/main/assets/sprite_sweeping.h`
- Modify: `firmware/main/scene.h` — add new `clawd_anim_id_t` values
- Modify: `firmware/main/scene.c` — add `anim_defs[]` entries, include new headers
- Modify: `firmware/main/ui_manager.c` — update `status_to_anim()` mapping

- [ ] **Step 1: Generate sprite headers from PNG frames**

For each animation, run:

```bash
python tools/png2rgb565.py <frames_dir> firmware/main/assets/sprite_<name>.h --name <name>
```

- [ ] **Step 2: Add new enum values to `scene.h`**

```c
typedef enum {
    CLAWD_ANIM_IDLE,
    CLAWD_ANIM_ALERT,
    CLAWD_ANIM_HAPPY,
    CLAWD_ANIM_SLEEPING,
    CLAWD_ANIM_DISCONNECTED,
    CLAWD_ANIM_THINKING,
    CLAWD_ANIM_TYPING,
    CLAWD_ANIM_JUGGLING,
    CLAWD_ANIM_BUILDING,
    CLAWD_ANIM_CONFUSED,
    CLAWD_ANIM_SWEEPING,
} clawd_anim_id_t;
```

- [ ] **Step 3: Add `anim_defs[]` entries in `scene.c`**

Include the new headers and add entries with appropriate `frame_ms`, `looping` (all `true` except `SWEEPING`), dimensions, and `y_offset`.

- [ ] **Step 4: Update `status_to_anim()` in `ui_manager.c`**

Replace the placeholder mappings:

```c
static clawd_anim_id_t status_to_anim(display_status_t status) {
    switch (status) {
    case DISPLAY_STATUS_SLEEPING:  return CLAWD_ANIM_SLEEPING;
    case DISPLAY_STATUS_IDLE:      return CLAWD_ANIM_IDLE;
    case DISPLAY_STATUS_THINKING:  return CLAWD_ANIM_THINKING;
    case DISPLAY_STATUS_WORKING_1: return CLAWD_ANIM_TYPING;
    case DISPLAY_STATUS_WORKING_2: return CLAWD_ANIM_JUGGLING;
    case DISPLAY_STATUS_WORKING_3: return CLAWD_ANIM_BUILDING;
    case DISPLAY_STATUS_CONFUSED:  return CLAWD_ANIM_CONFUSED;
    case DISPLAY_STATUS_SWEEPING:  return CLAWD_ANIM_SWEEPING;
    default:                       return CLAWD_ANIM_IDLE;
    }
}
```

- [ ] **Step 5: Build firmware**

Run: `cd firmware && idf.py build`
Expected: BUILD SUCCESS

- [ ] **Step 6: Build simulator**

Run: `cd simulator && cmake -B build && cmake --build build`
Expected: BUILD SUCCESS

- [ ] **Step 7: Commit**

```bash
git add firmware/main/assets/sprite_*.h firmware/main/scene.h firmware/main/scene.c firmware/main/ui_manager.c
git commit -m "feat(firmware): add 6 new working animation sprites and wire to display status"
```

### Task 12: End-to-end simulator test

**Files:**
- Create: `simulator/scenarios/working_animations.json`

- [ ] **Step 1: Create scenario file**

Create `simulator/scenarios/working_animations.json`:

```json
{
  "events": [
    {"delay_ms": 500, "action": "connect"},
    {"delay_ms": 1000, "json": "{\"action\":\"set_status\",\"status\":\"thinking\"}"},
    {"delay_ms": 2000, "json": "{\"action\":\"set_status\",\"status\":\"working_1\"}"},
    {"delay_ms": 2000, "json": "{\"action\":\"set_status\",\"status\":\"working_2\"}"},
    {"delay_ms": 2000, "json": "{\"action\":\"set_status\",\"status\":\"working_3\"}"},
    {"delay_ms": 2000, "json": "{\"action\":\"set_status\",\"status\":\"idle\"}"},
    {"delay_ms": 2000, "json": "{\"action\":\"set_status\",\"status\":\"confused\"}"},
    {"delay_ms": 2000, "json": "{\"action\":\"set_status\",\"status\":\"sweeping\"}"},
    {"delay_ms": 500, "json": "{\"action\":\"set_status\",\"status\":\"idle\"}"},
    {"delay_ms": 3000, "json": "{\"action\":\"set_status\",\"status\":\"sleeping\"}"},
    {"delay_ms": 2000, "action": "disconnect"}
  ]
}
```

- [ ] **Step 2: Run scenario in headless mode with screenshots**

Run:

```bash
./simulator/build/clawd-tank-sim --headless \
  --scenario simulator/scenarios/working_animations.json \
  --screenshot-dir ./shots/ --screenshot-on-event
```

Expected: Screenshots captured for each state transition. Verify visually that animations change correctly.

- [ ] **Step 3: Commit**

```bash
git add simulator/scenarios/working_animations.json
git commit -m "test(simulator): add working animations scenario for end-to-end testing"
```
