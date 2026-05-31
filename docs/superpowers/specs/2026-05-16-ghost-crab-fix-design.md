# Ghost Crab Fix Design

## Overview

Eliminate "ghost crabs" — session entries that linger on the display after the underlying Claude Code instance is gone. Today, dead sessions are only cleaned up by a 10-minute staleness timeout (`_evict_stale_sessions` in `daemon.py:352`) and by `SessionEnd` hooks, but `SessionEnd` is not fired on SIGKILL (terminal close, force-quit, crash) and is racy on `/clear` (new `SessionStart` may arrive before the old `SessionEnd`). The fix introduces process-liveness tracking using the Claude Code session PID, plus PID-based deduplication on `SessionStart`, so dead and replaced sessions are evicted promptly without depending on the `SessionEnd` hook firing.

## Root Causes Addressed

| Cause | Today's behavior | Fix |
|-------|------------------|-----|
| SIGKILL / terminal close — `SessionEnd` never fires | Crab persists ≤10 min until staleness eviction | Liveness polling task detects dead PID, evicts within 30s |
| `/clear` race — new `SessionStart` arrives before/without matching `SessionEnd` | Old session lingers ≤10 min | PID-based dedup on `SessionStart` evicts prior session sharing the same Claude Code PID |
| Daemon restart mid-session — stored PIDs may be stale or recycled | n/a (no PID tracking today) | Drop stored PIDs on `load_sessions()`; next hook event re-stamps |
| macOS sleep/wake — wall-clock staleness fires mass-eviction on resume | Pre-existing bug, ≤10 min crabs may disappear after wake | Switch `last_event` to `time.monotonic()` so sleep time doesn't count |

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| PID resolution | Ancestor walk via `ps`, in `clawd-tank-notify` | Mirrors battle-tested pattern in `claude-plugins/agent-bus/lib/common.sh:_find_claude_pid`. Handles direct exec, shell wrapper, subagent hooks (walks past child to parent claude). |
| Liveness primitive | `os.kill(pid, 0)` | Matches agent-bus pattern. No `psutil` dependency. Accepts small PID-recycle risk (mitigated by freshness window for dedup, and short polling cadence). |
| Polling cadence | 30s | Same as staleness checker; bounds worst-case ghost-crab visibility to 30s instead of 10 min. |
| PID stamping frequency | Every event | Resilient to daemon restart; subagent corruption avoided because the ancestor walk always resolves to the parent `claude` process. |
| Dedup freshness window | 60s (monotonic) | Defends against PID recycling: only evict an existing session with the same PID if it was active very recently (true `/clear` case). |
| `sessions.json` PID on load | Drop, do not validate | Simpler than persisting+validating `create_time` tuples. Next hook re-stamps. Sessions without a PID fall through to staleness eviction (existing safety net). |
| Staleness timeout | Keep 10 min | Becomes a fallback for sessions where PID resolution failed. Reviewer-recommended; PID liveness handles the common case. |
| `source` / `reason` capture | Log only, no behavioral branches | Cheap to capture, useful for debugging. PID dedup already handles `/clear`; further branching is YAGNI. |
| External dependency | None added | `os.kill` is stdlib. `ps` is on every Unix system. Hook script remains stdlib-only. |
| Empirical verification | Diagnostic step before implementation | Confirms ancestor walk finds `claude` from a `type: command` hook on the user's actual install. Agent-bus runs from the Bash tool (different invocation path), so the assumption isn't transitive. |

## PID Resolution

Port the agent-bus `_find_claude_pid` shell logic into Python stdlib inside the embedded `clawd-tank-notify` script. The script invokes `ps` once per ancestor, walking from its own `os.getppid()` upward until it finds a process matching the `claude` identification rules, or reaches PID 1.

### Identification rules (in order)

For each ancestor PID `p`:

1. **Strict `comm` match.** `ps -o comm= -p p` → trimmed result equals `"claude"`. This catches the native Claude Code binary.
2. **Argv regex fallback.** `ps -o command= -p p` → matches `(^|/)claude($|[[:space:]])`. This catches `node /path/to/claude` style installs.
3. Otherwise: walk to `ps -o ppid= -p p`.

### Negative rule — what we deliberately do NOT do

**No loose `*claude*` substring on argv.** The Bash tool spawns each command as `zsh -c '… ~/.claude/shell-snapshots/…'`, and a loose substring would latch onto that transient zsh, writing a doomed PID. Agent-bus's comment block on `lib/common.sh:36-47` documents this exact pitfall; we follow the same discipline.

### Fallback

If the walk finds no match, fall back to `os.getppid()` raw. Better to send something than nothing — the daemon's liveness check will quickly detect the wrong-PID case (transient shell exits immediately) and evict.

### Cost

One `ps` invocation per ancestor (typically 2-4 levels deep). At the worst-case hook firing rate (one per tool call, ~20/min during heavy use), this is ≤80 `ps` invocations per minute — negligible.

## Hook Payload Extension

`clawd-tank-notify` adds two new fields to every outbound message:

```python
{
    # existing fields…
    "pid": <resolved claude PID, or os.getppid() fallback>,
}
```

For `SessionStart` specifically:
```python
{
    "event": "session_start",
    "session_id": "...",
    "project": "...",
    "pid": <resolved>,
    "source": <hook payload "source" field>,
}
```

For `SessionEnd`:
```python
{
    "event": "dismiss",
    "hook": "SessionEnd",
    "session_id": "...",
    "pid": <resolved>,
    "reason": <hook payload "reason" field>,
}
```

Both `protocol.py` (used by tests) and the embedded `NOTIFY_SCRIPT` in `hooks.py` must be updated in sync.

## Daemon Changes

### Per-session state additions

`_session_states[session_id]` gains:

```python
{
    "pid": <int or None>,           # last resolved claude PID
    "last_event_monotonic": <float>, # time.monotonic() at last event
    # existing: state, last_event, tool_name, subagents, project
}
```

`last_event` (wall-clock) stays for display/debug purposes. `last_event_monotonic` is the new basis for staleness eviction.

### `_handle_message` flow

1. Resolve `pid = msg.get("pid")` and `now_mono = time.monotonic()`.
2. **PID dedup check** — only on `event == "session_start"`:
   - For each `(sid, state)` in `_session_states` where `sid != incoming_sid`:
     - If `state["pid"] == pid` AND `now_mono - state["last_event_monotonic"] < 60.0`:
       - Evict that session: `_session_states.pop(sid)`, `_session_order = [(s,d) for s,d in _session_order if s != sid]`, `_active_notifications.pop(sid, None)`
       - Log: `"PID dedup: evicting session %s (PID %d reused by new session %s)"`
3. Proceed with existing `_update_session_state` logic.
4. After update, stamp `cur["pid"] = pid` and `cur["last_event_monotonic"] = now_mono`.

### Liveness polling task

New async task `_liveness_checker`, started in `run()` alongside `_staleness_task`. Loop:

```python
async def _liveness_checker(self):
    while self._running:
        await asyncio.sleep(30)
        dead = []
        for sid, state in self._session_states.items():
            pid = state.get("pid")
            if pid is None:
                continue  # fall through to staleness eviction
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                dead.append(sid)
            except PermissionError:
                pass  # PID belongs to another user — unlikely, treat as alive
        for sid in dead:
            logger.info("Liveness: evicting session %s (PID %d gone)", sid[:12], self._session_states[sid].get("pid"))
            del self._session_states[sid]
            self._session_order = [(s, d) for s, d in self._session_order if s != sid]
            self._active_notifications.pop(sid, None)
        if dead:
            self._persist_sessions()
            await self._broadcast_display_state_if_changed()
```

### Staleness eviction switch to monotonic

`_evict_stale_sessions` changes its comparison basis:

```python
now_mono = time.monotonic()
stale = [
    sid for sid, s in self._session_states.items()
    if now_mono - s.get("last_event_monotonic", now_mono) > self._session_staleness_timeout
]
```

Sessions loaded from disk (post-restart) get a fresh `last_event_monotonic = time.monotonic()` so they aren't immediately evicted.

### `session_store.py` changes

**`save_sessions`:** Persist `pid` as-is; do NOT persist `last_event_monotonic` (monotonic time is meaningless across process restarts).

**`load_sessions`:** Drop the `pid` field from every loaded entry (set to `None`). Stamp `last_event_monotonic = time.monotonic()` for each. Wall-clock `last_event` is preserved as before for display.

Rationale: stored PIDs are unsafe across daemon restart (PID recycling, especially after reboot). Liveness check skips sessions with `pid=None`, so they rely on the 10-min staleness fallback until the next hook event re-stamps the PID.

## Hook Registration

No new hooks added. Existing `HOOKS_CONFIG` in `hooks.py` is unchanged. The PID stamping happens inside the existing `NOTIFY_SCRIPT` — no installer changes needed.

The embedded `NOTIFY_SCRIPT` version comment / hash will change, which means `are_hooks_installed()` (and the menu bar's startup auto-update) will detect the change and reinstall the script. This is desired behavior.

## Empirical Verification Step

Before merging, run a one-shot diagnostic to confirm `_find_claude_pid` resolution works inside a Claude Code `type: command` hook on the user's install. The diagnostic:

1. Adds a temporary hook entry that logs `os.getppid()`, the ancestor chain (each PID's `comm` and `command`), and the resolved PID, to `~/.clawd-tank/pid-diagnostic.log`.
2. User triggers each hook type once (SessionStart, PreToolUse, Stop, SubagentStart).
3. Inspect the log to confirm resolution finds the same long-lived `claude` PID across all events.
4. Remove the diagnostic.

This is captured as Step 0 in the implementation plan.

## Testing Strategy

### Unit tests — `tests/test_protocol.py`

- `SessionStart` payload with `source` field → daemon message includes `source`.
- `SessionEnd` payload with `reason` field → daemon message includes `reason`.
- All payloads include `pid` field.

### Unit tests — new `tests/test_pid_resolution.py`

- Mock `subprocess.run` for `ps`; verify ancestor walk finds `claude` ancestor.
- Verify `node /path/to/claude` fallback regex matches.
- Verify zsh shell-snapshot ancestor does NOT match (negative case).
- Verify fallback to `getppid()` when no claude ancestor.

### Unit tests — `tests/test_session_state.py` (new cases)

- `_handle_message` with new `SessionStart` PID matching an existing session within 60s → old session evicted, `_session_order` scrubbed.
- Same scenario but `last_event_monotonic` is 120s old → no dedup (PID recycle assumption).
- `_liveness_checker` evicts sessions whose PID returns `ProcessLookupError`.
- `_liveness_checker` skips sessions with `pid=None`.
- `_evict_stale_sessions` uses monotonic time, not wall-clock.
- `load_sessions()` drops PIDs and stamps fresh monotonic.

### Integration test

A black-box test using a fake `claude`-named process:
1. Start a `sleep 60` subprocess and rename `argv[0]` to `claude` (via `prctl` on Linux; on macOS use a shell wrapper).
2. From a grandchild of that process, invoke the notify script.
3. Verify the daemon receives the `sleep` parent's PID, not the grandchild's.
4. Kill the parent. Verify the daemon evicts within 30s.

## Migration / Compatibility

- **Existing `sessions.json` files**: load gracefully — entries without `pid` field default to `None`; old format stays compatible (we ignore PID on load anyway).
- **Mixed-version operation**: if a user has an old `clawd-tank-notify` (no `pid` field) and a new daemon, sessions are tracked but never get PID-evicted — falls through to staleness. Daemon code must handle `pid` absent or `None` gracefully throughout.
- **Script update**: `are_hooks_installed()` currently only checks that hook entries reference `HOOK_COMMAND`, not the script content itself. The menu bar app's existing startup auto-update (which writes `NOTIFY_SCRIPT` to disk when the installed daemon version is outdated, per CLAUDE.md) will replace the script on next launch. Implementation should verify this path actually runs for content-only changes; if not, add a content hash or version comment check to force script rewrite when `NOTIFY_SCRIPT` changes.

## Out of Scope

- Removing `StopFailure` from `HOOKS_CONFIG` (separate issue — it IS a valid hook in current Claude Code; the reporter's claim is based on an outdated version).
- Acting on `source`/`reason` field values (captured for logging only in this fix; future behavioral logic is separately motivated).
- Cross-platform PID resolution beyond macOS (Linux works trivially via the same `ps` invocations; Windows is not a target for the host daemon).
- Replacing the staleness eviction entirely (kept as a safety net).
