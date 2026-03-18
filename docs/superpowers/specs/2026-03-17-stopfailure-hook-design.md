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

## Protocol Flow

### Hook → Daemon Message

In both `protocol.py` and the embedded script in `hooks.py`:

```
StopFailure payload → {
    "event": "add",
    "hook": "StopFailure",
    "session_id": <session_id>,
    "project": <project name from cwd>,
    "message": <error reason from payload, fallback "API error">
}
```

The `StopFailure` hook payload should include error details (e.g., `stop_reason` or `error` field). Extract into `message` for the notification card. Fall back to `"API error"` if no detail is available.

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

## Firmware Changes

### New Animation: `CLAWD_ANIM_DIZZY`

- Added to `clawd_anim_t` enum in `scene.h`
- New sprite asset `sprite_dizzy.h` via the sprite pipeline
- Visual: Clawd standing with X eyes, band-aid on forehead, 2-3 pixel-art stars orbiting in an elliptical path above his head
- Looping animation, ~8fps
- Sprite size: taller than idle (~100-120px height) to accommodate star orbit

### Scene Integration (`scene.c`)

- Add DIZZY to sprite definition table with frame timing
- Add `"dizzy"` string mapping for `set_sessions` animation name parsing

### BLE Parsing (`ble_service.c` + `sim_ble_parse.c`)

- Parse `"dizzy"` animation name in `set_sessions` action
- Parse `"alert"` field on `add` action, store in `ble_evt_t` struct

### LED Triple Red Flash (`rgb_led.c`)

New function `rgb_led_flash_error()`:
- Three quick red pulses: red on → off → on → off → on → fade out
- Uses the existing `esp_timer` infrastructure
- Distinct from the rainbow palette cycle in `rgb_led_flash()`

### UI Manager (`ui_manager.c`)

In the `BLE_EVT_NOTIF_ADD` handler:
- Check for `alert` field in event struct
- If `"error"`: call `rgb_led_flash_error()` instead of `rgb_led_flash()`

## Notification Card

- Uses existing card rendering in `notification_ui.c` — no changes
- Project name from `cwd` (same as `Stop`)
- Message: error reason from payload (e.g., "Rate limit reached"), fallback "API error"
- Dismisses on `UserPromptSubmit` or `SessionEnd` — existing dismiss path
- No special accent color — DIZZY animation + red LED flash already distinguish errors

## Testing

### Python Tests (`host/tests/`)

- `test_protocol.py`: `StopFailure` → daemon message conversion, verify `alert` field in BLE payload
- Daemon tests: `"error"` state transition, `"dizzy"` in computed display state, transitions out of error (UserPromptSubmit, SessionEnd, tool_use)
- V1 fallback: `"dizzy"` maps to `"confused"` in `display_state_to_v1_payload`

### C Unit Tests (`firmware/test/`)

- `add` action with `"alert": "error"` field parsed correctly

### Manual Testing (Simulator)

- Via TCP: send `{"action":"add","id":"test","project":"test","message":"Rate limited","alert":"error"}` followed by `{"action":"set_sessions","anims":["dizzy"],"ids":[1],"subagents":0}`
- Visual confirmation of DIZZY animation rendering (stars orbit, X eyes, band-aid)

## Scope Exclusions

- No notification type filtering — all `StopFailure` events are relevant
- No retry/recovery logic — daemon doesn't detect rate limit clearing
- No special card accent color for errors
- No changes to `sessions.json` format — `"error"` is just another state string
- No v1 firmware changes — host-side v1 mapping only
