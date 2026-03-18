# StopFailure Hook Integration Design

## Overview

Integrate the new Claude Code `StopFailure` hook (added in v2.1.78) to visually indicate when a session hits an API error (rate limit, auth failure, etc.). Currently, failed sessions sit idle until staleness eviction — this feature makes failures immediately visible through a new DIZZY animation, a notification card with the error reason, and a triple red LED flash.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Error state persistence | Persistent until user action | Session is genuinely stuck; idle would be misleading |
| Animation | New DIZZY (X eyes, band-aid, orbiting stars) | Visually distinct from CONFUSED; conveys "something broke" |
| LED behavior | Triple red flash | Distinguishes from normal orange palette cycle on regular notifications |
| Session state name | `"error"` | Semantic; animation name `"dizzy"` is the visual concept |
| Staleness eviction | Normal (no exemption) | If stale 10+ min, session is dead — no point keeping dizzy crab |
| Architecture | StopFailure as `"add"` event variant | Follows existing Stop/Notification pattern exactly |
| V1 fallback | `"dizzy"` → `"confused"` | V1 firmware has no DIZZY sprite; confused is close enough |

## Hook Registration

Add `StopFailure` to `HOOKS_CONFIG` in `hooks.py`:

```python
"StopFailure": [
    {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
],
```

No matcher needed — all stop failures are relevant.

**Deployment note:** Adding `StopFailure` to `HOOKS_CONFIG` means existing installations will have `are_hooks_installed()` return `False` on next startup, triggering auto-reinstall of hooks. This is the desired behavior — no manual "Install Hooks" click needed.

## Protocol Flow

### Hook → Daemon Message

Both `protocol.py` and the embedded `NOTIFY_SCRIPT` in `hooks.py` must be updated in sync. The embedded script duplicates the conversion logic as a standalone stdlib-only Python script.

```
StopFailure payload → {
    "event": "add",
    "hook": "StopFailure",
    "session_id": <session_id>,
    "project": <project name from cwd>,
    "message": <error reason from payload, fallback "API error">
}
```

**Payload schema discovery:** The exact `StopFailure` payload fields are not yet documented by Claude Code. Implementation should log the raw payload on first encounter to confirm field names. Try `hook.get("error", "")` and `hook.get("stop_reason", "")` with fallback to `"API error"`. Update the extraction logic once the actual schema is confirmed.

### Daemon Message → BLE Payload

In `daemon_message_to_ble_payload`, the `"add"` branch gains an `"alert"` field when hook is `"StopFailure"`:

```json
{"action": "add", "id": "...", "project": "...", "message": "Rate limited", "alert": "error"}
```

The `"alert": "error"` field tells the firmware to flash red instead of orange.

## Session State Machine

### New State: `"error"`

In `_update_session_state`:

```
event="add", hook="StopFailure" → state = "error"
```

### Transitions Out of `"error"`

| Event | New State | Reason |
|-------|-----------|--------|
| `UserPromptSubmit` | `"thinking"` | User resumed the session |
| `SessionEnd` | removed | Session ended |
| `tool_use` | `"working"` | Session recovered |
| Staleness timeout | removed | Normal eviction, no exemption |

### Display State Mapping

In `_compute_display_state`:

```python
elif state["state"] == "error":
    anims.append("dizzy")
```

### V1 Fallback

In `display_state_to_v1_payload`, add `"dizzy"` to the confused check:

```python
elif any(a in ("confused", "dizzy") for a in state.get("anims", [])):
    status = "confused"
```

V1 devices will show CONFUSED instead of DIZZY — acceptable since V1 is legacy and CONFUSED is the closest available animation.

## Firmware Changes

### New Animation: `CLAWD_ANIM_DIZZY`

- Added to `clawd_anim_t` enum in `scene.h`
- New sprite asset `sprite_dizzy.h` via the sprite pipeline
- Visual: Clawd standing with X eyes, band-aid on forehead, 2-3 pixel-art stars orbiting in an elliptical path above his head
- Looping animation, ~8fps
- Sprite size: taller than idle (~100-120px height) to accommodate star orbit
- Memory estimate: ~120×100px × 3 bytes (RGB565A8) = ~36 KB per frame buffer (lazy-allocated per slot, within the ~200 KB free heap budget). Flash cost depends on frame count and RLE compression ratio.

### Scene Integration (`scene.c`)

- Add DIZZY to sprite definition table with frame timing
- Add `"dizzy"` string mapping for `set_sessions` animation name parsing in both `ble_service.c` and `sim_ble_parse.c`

### BLE Parsing (`ble_service.c` + `sim_ble_parse.c`)

- Parse `"dizzy"` animation name in `parse_anim_name` function
- Parse `"alert"` field on `add` action JSON
- Store alert in `ble_evt_t` struct — add new field:

```c
uint8_t alert;  /* 0=none, 1=error */
```

Set to `1` when JSON contains `"alert": "error"`, `0` otherwise. Both `ble_service.c` and `sim_ble_parse.c` must parse this consistently.

### LED Triple Red Flash (`rgb_led.c`)

New function `rgb_led_flash_error()`:
- Three red pulses: 150ms on → 100ms off → 150ms on → 100ms off → 150ms on → fade out (~200ms). Total duration ~850ms.
- Shares the existing `s_timer` and `s_steps_left` mechanism. Add a mode flag (`s_flash_mode`) to `timer_cb` to switch between palette-cycle (normal) and triple-red-pulse (error) behavior.
- Calling `rgb_led_flash_error()` preempts any in-progress flash (calls `esp_timer_stop()` first, same as `rgb_led_flash()`).

### UI Manager (`ui_manager.c`)

In the `BLE_EVT_NOTIF_ADD` handler:
- Check `evt->alert` field
- If `alert == 1`: call `rgb_led_flash_error()` instead of `rgb_led_flash()`

## Notification Card

- Uses existing card rendering in `notification_ui.c` — no changes
- Project name from `cwd` (same as `Stop`)
- Message: error reason from payload (e.g., "Rate limit reached"), fallback "API error"
- Dismisses on `UserPromptSubmit` or `SessionEnd` via the existing `dismiss` event → `daemon_message_to_ble_payload` → BLE dismiss action path
- If a `Stop` notification already exists for the same session, `StopFailure` overwrites it (keyed by `session_id` in `_active_notifications`). This is correct — the error supersedes "Waiting for input".
- No special accent color — DIZZY animation + red LED flash already distinguish errors

## Implementation Order

Firmware animation parsing must be deployed before the daemon starts sending `"dizzy"` animation names. If `parse_anim_name` receives an unknown name, it returns `-1` and the slot is skipped — the session crab would vanish. Order:

1. **Firmware first:** Add `CLAWD_ANIM_DIZZY` enum, sprite asset, `parse_anim_name("dizzy")`, `alert` field parsing, `rgb_led_flash_error()`.
2. **Simulator:** Same changes via shared source files + shims.
3. **Host last:** Hook registration, protocol conversion, session state machine, display state mapping.

For development/testing, simulator changes land alongside firmware (shared source). The host changes can be tested against the simulator before flashing hardware.

## Testing

### Python Tests (`host/tests/`)

- `test_protocol.py`: `StopFailure` → daemon message conversion, verify `alert` field in BLE payload
- Daemon tests: `"error"` state transition, `"dizzy"` in computed display state, transitions out of error (UserPromptSubmit, SessionEnd, tool_use)
- V1 fallback: `"dizzy"` maps to `"confused"` in `display_state_to_v1_payload`
- Rapid succession: `Stop` then `StopFailure` on same session within 100ms — verify card overwrite and state transition to `"error"`

### C Unit Tests (`firmware/test/`)

- `add` action with `"alert": "error"` field parsed correctly, `alert` set to `1`
- `add` action without `"alert"` field, `alert` set to `0`

### Manual Testing (Simulator)

- Via TCP: send `{"action":"add","id":"test","project":"test","message":"Rate limited","alert":"error"}` followed by `{"action":"set_sessions","anims":["dizzy"],"ids":[1],"subagents":0}`
- Visual confirmation of DIZZY animation rendering (stars orbit, X eyes, band-aid)
- Verify triple red LED flash (simulator may need visual indicator or log output)

## Scope Exclusions

- No notification type filtering — all `StopFailure` events are relevant
- No retry/recovery logic — daemon doesn't detect rate limit clearing
- No special card accent color for errors
- No changes to `sessions.json` format — `"error"` is just another state string
- No v1 firmware changes — host-side v1 mapping only (v1 devices show CONFUSED instead of DIZZY)
