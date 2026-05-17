# Ghost Crab Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop ghost crabs (lingering display entries for dead Claude Code sessions) by tracking the Claude Code PID per session, polling for liveness, and deduplicating `/clear`-replaced sessions by PID.

**Architecture:** `clawd-tank-notify` walks the process tree to find the long-lived `claude` ancestor PID and stamps it on every message. The daemon stores `(pid, last_event_monotonic)` per session, runs a 30s liveness poll (`os.kill(pid, 0)`), and evicts dead sessions. On `SessionStart`, PID-based dedup catches `/clear` even when `SessionEnd` is delayed or missing. Wall-clock staleness eviction switches to monotonic time so macOS sleep doesn't cause mass-eviction on wake.

**Tech Stack:** Python stdlib only in the hook script (`subprocess.run(["ps", ...])` for ancestor walk); `os.kill`, `time.monotonic`, `asyncio` in the daemon. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-16-ghost-crab-fix-design.md`

---

## File Structure

**Create:**
- `host/clawd_tank_daemon/pid_resolver.py` — Tested standalone implementation of `find_claude_pid()`. Imported nowhere at runtime; exists for unit testing the resolution logic. The actual logic is duplicated into the embedded `NOTIFY_SCRIPT` string (see "duplication discipline" below).
- `host/tests/test_pid_resolver.py` — Unit tests for `find_claude_pid()` with mocked `subprocess.run`.
- `host/tests/test_notify_script.py` — Integration test that writes `NOTIFY_SCRIPT` to a temp file and runs it as a subprocess to verify end-to-end PID resolution + payload conversion.

**Modify:**
- `host/clawd_tank_menubar/hooks.py` — Update the embedded `NOTIFY_SCRIPT` to include `_find_claude_pid()` and stamp `pid` (plus `source` for SessionStart, `reason` for SessionEnd) on every outbound message.
- `host/clawd_tank_daemon/protocol.py` — Flow `pid`, `source`, `reason` through `hook_payload_to_daemon_message`.
- `host/clawd_tank_daemon/daemon.py` — Add `pid` and `last_event_monotonic` to session state; add PID dedup on `session_start`; add `_liveness_checker` task; switch `_evict_stale_sessions` to monotonic time.
- `host/clawd_tank_daemon/session_store.py` — `save_sessions`: drop `last_event_monotonic` (in-memory only). `load_sessions`: drop `pid` (unsafe across restart).
- `host/tests/test_protocol.py` — Add tests for new fields.
- `host/tests/test_session_state.py` — Add tests for PID dedup, liveness eviction, monotonic staleness.
- `host/tests/test_session_store.py` — Add tests for new persistence rules.

**Duplication discipline:** `pid_resolver.py` and the `_find_claude_pid` block inside `NOTIFY_SCRIPT` (a string literal in `hooks.py`) are deliberate duplicates — the embedded notify script must be stdlib-only and standalone (installed to `~/.clawd-tank/clawd-tank-notify`, runs without the daemon's venv). Mark both with a comment: `# Mirrors pid_resolver.py — keep in sync`. This matches the existing `hook_payload_to_daemon_message` / `NOTIFY_SCRIPT` duplication pattern in the codebase.

---

## Task 1: Empirical PID resolution verification (manual checkpoint)

**Files:**
- Create (temporary): `/tmp/clawd-pid-diag.py`
- Modify (temporary): `~/.claude/settings.json` (revert after)

**Purpose:** Confirm that walking up from `os.getppid()` inside a Claude Code `type: command` hook can find a process whose `ps -o comm=` is `"claude"` (or whose `ps -o command=` matches `(^|/)claude($|\s)`). The agent-bus uses this pattern but is invoked via the Bash tool, which may use a different process tree. We need direct evidence for the hook path.

- [ ] **Step 1: Write the diagnostic script**

Create `/tmp/clawd-pid-diag.py`:

```python
#!/usr/bin/env python3
import os, sys, subprocess, json, datetime
from pathlib import Path

def ps(field, pid):
    try:
        r = subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=1.0)
        return r.stdout.strip()
    except Exception:
        return ""

def walk_ancestors(start_pid, max_depth=20):
    chain = []
    pid = start_pid
    for _ in range(max_depth):
        if pid <= 1:
            break
        chain.append({"pid": pid, "comm": ps("comm", pid), "command": ps("command", pid)})
        ppid_str = ps("ppid", pid)
        try:
            pid = int(ppid_str)
        except ValueError:
            break
    return chain

payload = sys.stdin.read()
try:
    parsed = json.loads(payload)
    hook = parsed.get("hook_event_name", "?")
except Exception:
    hook = "?"

log_path = Path.home() / ".clawd-tank" / "pid-diagnostic.log"
log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("a") as f:
    f.write(json.dumps({
        "ts": datetime.datetime.now().isoformat(),
        "hook": hook,
        "getppid": os.getppid(),
        "ancestors": walk_ancestors(os.getppid()),
    }) + "\n")
```

```bash
chmod +x /tmp/clawd-pid-diag.py
```

- [ ] **Step 2: Wire the diagnostic into Claude Code temporarily**

Open `~/.claude/settings.json` and add (or merge into) the `hooks` block — these are ADDITIONS, do not remove existing hooks:

```json
"SessionStart": [
  {"hooks": [{"type": "command", "command": "/tmp/clawd-pid-diag.py"}]}
],
"PreToolUse": [
  {"hooks": [{"type": "command", "command": "/tmp/clawd-pid-diag.py"}]}
],
"Stop": [
  {"hooks": [{"type": "command", "command": "/tmp/clawd-pid-diag.py"}]}
],
"SubagentStart": [
  {"hooks": [{"type": "command", "command": "/tmp/clawd-pid-diag.py"}]}
]
```

(If existing entries for these hooks reference the Clawd Tank notify script, ADD the diagnostic alongside it in the same `hooks: []` array.)

- [ ] **Step 3: Trigger each hook event**

In a fresh terminal, start `claude`. Then inside the session:
1. Send any prompt (triggers `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop`).
2. Use the Agent tool to dispatch a subagent (triggers `SubagentStart`).
3. `/exit` to close (triggers `SessionEnd`).

- [ ] **Step 4: Inspect the log**

```bash
cat ~/.clawd-tank/pid-diagnostic.log | python3 -m json.tool --json-lines
```

Verify:
- Every entry's `ancestors` chain contains a PID where `comm == "claude"` OR `command` matches `(^|/)claude($|\s)`.
- The matched PID is the SAME across SessionStart, PreToolUse, Stop entries from the same `claude` session.
- The matched PID for `SubagentStart` entries is ALSO the parent `claude` session's PID (not a different one) — this is the critical subagent-doesn't-corrupt-tracking check.

- [ ] **Step 5: Decision gate**

If verification passes: proceed to Task 2. Remove diagnostic entries from `~/.claude/settings.json` and delete `/tmp/clawd-pid-diag.py` and `~/.clawd-tank/pid-diagnostic.log`.

If verification fails (no `claude` ancestor found, or different PIDs from same session): STOP. The design assumption is wrong. Report findings to the human, revise the spec.

- [ ] **Step 6: Commit nothing**

No code committed yet. This task produces evidence, not code.

---

## Task 2: Create `pid_resolver` module with TDD

**Files:**
- Create: `host/clawd_tank_daemon/pid_resolver.py`
- Test: `host/tests/test_pid_resolver.py`

- [ ] **Step 1: Write the failing tests**

Create `host/tests/test_pid_resolver.py`:

```python
"""Tests for find_claude_pid — walks process ancestors via `ps` to find the
long-lived Claude Code PID, mirroring claude-plugins/agent-bus/lib/common.sh.
"""

from unittest.mock import patch, MagicMock

from clawd_tank_daemon.pid_resolver import find_claude_pid


def _make_ps_mock(responses):
    """responses: dict of (field, pid) -> raw stdout string (will get a trailing newline).

    Each subprocess.run call gets args=["ps", "-o", "<field>=", "-p", "<pid>"].
    Returns MagicMock with .stdout matching the responses table, empty otherwise.
    """
    def side_effect(args, **kwargs):
        field = args[2].rstrip("=")
        pid = int(args[4])
        out = responses.get((field, pid), "")
        return MagicMock(stdout=out + "\n", returncode=0)
    return side_effect


def test_direct_parent_is_claude():
    """getppid() returns claude — return it directly."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=100):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 100): "claude",
            })
            assert find_claude_pid() == 100


def test_walks_past_shell_wrapper():
    """getppid() is zsh, ancestor is claude — walk and find it."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=200):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 200): "zsh",
                ("command", 200): "zsh -c /Users/me/.clawd-tank/clawd-tank-notify",
                ("ppid", 200): "300",
                ("comm", 300): "claude",
            })
            assert find_claude_pid() == 300


def test_node_install_matches_via_command_regex():
    """Node-wrapped install: comm is 'node', command starts with 'node /path/to/claude'."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=400):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 400): "node",
                ("command", 400): "node /opt/claude-code/cli.js",
            })
            assert find_claude_pid() == 400


def test_node_install_with_path_prefix_matches():
    """Command starts with /usr/local/bin/claude."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=500):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 500): "claude-wrapper",
                ("command", 500): "/usr/local/bin/claude --resume abc",
            })
            assert find_claude_pid() == 500


def test_shell_snapshot_does_NOT_match():
    """Critical negative case (agent-bus calls this out): the Bash tool spawns
    `zsh -c '... ~/.claude/shell-snapshots/...'`. A loose substring would match
    the zsh's argv. The strict `comm == "claude"` and `(^|/)claude($|\\s)` regex
    must NOT match this shell.
    """
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=600):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 600): "zsh",
                ("command", 600): "zsh -c source ~/.claude/shell-snapshots/snap-12345.sh; /tmp/cmd",
                ("ppid", 600): "1",  # walk terminates
            })
            # No claude ancestor found, falls back to getppid()
            assert find_claude_pid() == 600


def test_falls_back_to_getppid_when_no_claude_ancestor():
    """Walk reaches pid 1 without finding claude — return original getppid()."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=700):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({
                ("comm", 700): "zsh",
                ("command", 700): "zsh",
                ("ppid", 700): "1",
            })
            assert find_claude_pid() == 700


def test_falls_back_to_getppid_when_ps_fails():
    """ps returns empty (process gone mid-walk) — fall back gracefully."""
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=800):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = _make_ps_mock({})  # all empty
            assert find_claude_pid() == 800


def test_ps_timeout_does_not_raise():
    """subprocess timeout returns empty string, walk falls back."""
    import subprocess as sp
    with patch("clawd_tank_daemon.pid_resolver.os.getppid", return_value=900):
        with patch("clawd_tank_daemon.pid_resolver.subprocess.run") as run:
            run.side_effect = sp.TimeoutExpired(cmd="ps", timeout=1.0)
            assert find_claude_pid() == 900
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_pid_resolver.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'clawd_tank_daemon.pid_resolver'`.

- [ ] **Step 3: Implement `pid_resolver.py`**

Create `host/clawd_tank_daemon/pid_resolver.py`:

```python
"""Resolve the long-lived Claude Code session PID by walking the process tree.

Mirrors claude-plugins/agent-bus/lib/common.sh:_find_claude_pid. The embedded
NOTIFY_SCRIPT in clawd_tank_menubar/hooks.py duplicates this logic — keep them
in sync.
"""

import os
import re
import subprocess

_CLAUDE_ARGV_RE = re.compile(r"(^|/)claude($|\s)")


def _ps(field: str, pid: int) -> str:
    """Run `ps -o <field>= -p <pid>` and return trimmed stdout. Empty on error."""
    try:
        r = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True, text=True, timeout=1.0,
        )
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def find_claude_pid() -> int:
    """Walk from os.getppid() up the process tree to find the long-lived
    Claude Code PID. Falls back to os.getppid() if no `claude` ancestor found.

    Identification (in order, per ancestor):
      1. `ps -o comm=` exactly equals "claude" (native binary)
      2. `ps -o command=` matches (^|/)claude($|\\s) (node-wrapped install)

    Loose substring matching against argv is DELIBERATELY AVOIDED — see the
    agent-bus comment block (lib/common.sh:36-47) for the rationale.
    """
    start = os.getppid()
    pid = start
    while pid > 1:
        if _ps("comm", pid) == "claude":
            return pid
        if _CLAUDE_ARGV_RE.search(_ps("command", pid)):
            return pid
        ppid_str = _ps("ppid", pid)
        try:
            pid = int(ppid_str)
        except ValueError:
            break
    return start
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_pid_resolver.py -v
```
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git add host/clawd_tank_daemon/pid_resolver.py host/tests/test_pid_resolver.py
git commit -m "feat: add pid_resolver to find long-lived Claude Code PID via ancestor walk"
```

---

## Task 3: Flow `pid`/`source`/`reason` through `protocol.py`

**Files:**
- Modify: `host/clawd_tank_daemon/protocol.py:8-110` (`hook_payload_to_daemon_message`)
- Test: `host/tests/test_protocol.py`

- [ ] **Step 1: Write failing tests**

Append to `host/tests/test_protocol.py`:

```python
# --- PID, source, reason capture (ghost-crab fix) ---

def test_session_start_includes_pid_and_source():
    """SessionStart payload's pid and source fields flow through to daemon msg."""
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "SessionStart",
        "session_id": "s1",
        "cwd": "/foo/bar",
        "pid": 4242,
        "source": "clear",
    })
    assert msg["pid"] == 4242
    assert msg["source"] == "clear"


def test_session_end_includes_pid_and_reason():
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "SessionEnd",
        "session_id": "s1",
        "pid": 4242,
        "reason": "logout",
    })
    assert msg["pid"] == 4242
    assert msg["reason"] == "logout"


def test_pre_tool_use_includes_pid():
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "PreToolUse",
        "session_id": "s1",
        "cwd": "/foo",
        "tool_name": "Edit",
        "pid": 4242,
    })
    assert msg["pid"] == 4242


def test_stop_includes_pid():
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "Stop",
        "session_id": "s1",
        "cwd": "/foo",
        "pid": 4242,
    })
    assert msg["pid"] == 4242


def test_session_start_missing_pid_field_omits_it():
    """Backwards compat — old notify script without pid still produces valid msg."""
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "SessionStart",
        "session_id": "s1",
        "cwd": "/foo",
    })
    assert "pid" not in msg or msg.get("pid") is None
    assert msg.get("source") is None or "source" not in msg


def test_subagent_start_includes_pid():
    from clawd_tank_daemon.protocol import hook_payload_to_daemon_message
    msg = hook_payload_to_daemon_message({
        "hook_event_name": "SubagentStart",
        "session_id": "s1",
        "agent_id": "a1",
        "pid": 4242,
    })
    assert msg["pid"] == 4242
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_protocol.py -v -k "pid or source or reason"
```
Expected: all 6 new tests FAIL with `KeyError: 'pid'` or `assert None == 4242`.

- [ ] **Step 3: Update `protocol.py`**

Modify `host/clawd_tank_daemon/protocol.py`. At the top of `hook_payload_to_daemon_message` (around line 13), capture the PID once:

```python
def hook_payload_to_daemon_message(hook: dict) -> Optional[dict]:
    """Convert a Claude Code hook stdin payload to a daemon message.

    Returns None if the hook event is not relevant (should be ignored).
    """
    event_name = hook.get("hook_event_name", "")
    session_id = hook.get("session_id", "")
    cwd = hook.get("cwd", "")
    project = Path(cwd).name if cwd else ""
    pid = hook.get("pid")  # int or None; absent in older notify scripts
```

Then in EACH branch that returns a dict, add `"pid": pid` to the returned dict. For SessionStart specifically, also include `source`:

```python
    if event_name == "SessionStart":
        msg = {
            "event": "session_start",
            "session_id": session_id,
            "project": project,
            "pid": pid,
        }
        source = hook.get("source")
        if source is not None:
            msg["source"] = source
        return msg
```

For SessionEnd, also include `reason`:

```python
    if event_name == "SessionEnd":
        msg = {
            "event": "dismiss",
            "hook": "SessionEnd",
            "session_id": session_id,
            "pid": pid,
        }
        reason = hook.get("reason")
        if reason is not None:
            msg["reason"] = reason
        return msg
```

For all other branches that return dicts (`PreToolUse`, `PreCompact`, `Stop`, `StopFailure`, `Notification`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`), add `"pid": pid` to the returned dict. Example for `Stop`:

```python
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
            "pid": pid,
        }
```

Apply the same `"pid": pid` addition to every other returning branch.

- [ ] **Step 4: Run tests**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_protocol.py -v
```
Expected: all tests PASS (existing tests still green — they don't assert on absent `pid`).

- [ ] **Step 5: Commit**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git add host/clawd_tank_daemon/protocol.py host/tests/test_protocol.py
git commit -m "feat: capture pid, source, reason from hook payloads in protocol layer"
```

---

## Task 4: Update embedded `NOTIFY_SCRIPT` in `hooks.py`

**Files:**
- Modify: `host/clawd_tank_menubar/hooks.py:18-135` (the `NOTIFY_SCRIPT` literal)
- Test: `host/tests/test_notify_script.py` (new)

- [ ] **Step 1: Write the integration test**

Create `host/tests/test_notify_script.py`:

```python
"""End-to-end test of the embedded NOTIFY_SCRIPT: write it to a tempfile,
invoke as a subprocess with a hook payload on stdin, assert it writes the
expected JSON to a Unix socket.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from clawd_tank_menubar.hooks import NOTIFY_SCRIPT


def _run_script_with_payload(payload: dict, sock_path: str) -> dict:
    """Run NOTIFY_SCRIPT in a subprocess with payload on stdin; return the
    JSON message it sent over the Unix socket. Returns {} if nothing arrived."""
    received = {}

    def server():
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(1)
        srv.settimeout(5.0)
        try:
            conn, _ = srv.accept()
            data = b""
            conn.settimeout(2.0)
            while True:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                data += chunk
            conn.close()
            line = data.decode("utf-8").strip()
            if line:
                received.update(json.loads(line))
        except socket.timeout:
            pass
        finally:
            srv.close()

    t = threading.Thread(target=server)
    t.start()

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(NOTIFY_SCRIPT)
        script_path = f.name
    os.chmod(script_path, 0o755)

    try:
        # Override the socket path the script uses via env (we'll modify the
        # script to honor CLAWD_TANK_SOCKET if set — see Step 3).
        env = os.environ.copy()
        env["CLAWD_TANK_SOCKET"] = sock_path
        subprocess.run(
            [sys.executable, script_path],
            input=json.dumps(payload).encode("utf-8"),
            env=env,
            timeout=5.0,
            capture_output=True,
        )
    finally:
        os.unlink(script_path)

    t.join(timeout=3.0)
    return received


def test_notify_script_stamps_pid_on_session_start(tmp_path):
    sock_path = str(tmp_path / "sock")
    msg = _run_script_with_payload({
        "hook_event_name": "SessionStart",
        "session_id": "test-session-123",
        "cwd": str(tmp_path),
        "source": "startup",
    }, sock_path)
    assert msg.get("event") == "session_start"
    assert msg.get("session_id") == "test-session-123"
    assert isinstance(msg.get("pid"), int)
    assert msg["pid"] > 0
    assert msg.get("source") == "startup"


def test_notify_script_stamps_pid_on_stop(tmp_path):
    sock_path = str(tmp_path / "sock")
    msg = _run_script_with_payload({
        "hook_event_name": "Stop",
        "session_id": "s2",
        "cwd": str(tmp_path),
    }, sock_path)
    assert msg.get("event") == "add"
    assert msg.get("hook") == "Stop"
    assert isinstance(msg.get("pid"), int)


def test_notify_script_session_end_includes_reason(tmp_path):
    sock_path = str(tmp_path / "sock")
    msg = _run_script_with_payload({
        "hook_event_name": "SessionEnd",
        "session_id": "s3",
        "reason": "logout",
    }, sock_path)
    assert msg.get("event") == "dismiss"
    assert msg.get("hook") == "SessionEnd"
    assert msg.get("reason") == "logout"
    assert isinstance(msg.get("pid"), int)


def test_notify_script_irrelevant_hook_sends_nothing(tmp_path):
    sock_path = str(tmp_path / "sock")
    msg = _run_script_with_payload({
        "hook_event_name": "PostToolUse",  # not handled
        "session_id": "s4",
    }, sock_path)
    assert msg == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_notify_script.py -v
```
Expected: FAIL — `msg` either empty (no pid field yet) or assertions on `pid`/`source`/`reason` miss.

- [ ] **Step 3: Update `NOTIFY_SCRIPT` in `hooks.py`**

Replace the `NOTIFY_SCRIPT` constant in `host/clawd_tank_menubar/hooks.py:18-135` with this version:

```python
NOTIFY_SCRIPT = textwrap.dedent('''\
    #!/usr/bin/env python3
    """clawd-tank-notify - Claude Code hook handler for Clawd Tank.

    Reads hook payload from stdin, converts it to a daemon message,
    and forwards it via Unix socket. No external dependencies.
    """

    import json
    import os
    import re
    import socket
    import subprocess
    import sys
    from pathlib import Path

    SOCKET_PATH = os.environ.get(
        "CLAWD_TANK_SOCKET",
        str(Path.home() / ".clawd-tank" / "sock"),
    )

    _CLAUDE_ARGV_RE = re.compile(r"(^|/)claude($|\\s)")


    def _ps(field, pid):
        """Run `ps -o <field>= -p <pid>`, return trimmed stdout. Empty on error."""
        try:
            r = subprocess.run(
                ["ps", "-o", field + "=", "-p", str(pid)],
                capture_output=True, text=True, timeout=1.0,
            )
            return r.stdout.strip()
        except (subprocess.TimeoutExpired, OSError):
            return ""


    def _find_claude_pid():
        """Walk from os.getppid() up the process tree to find the long-lived
        Claude Code PID. Falls back to os.getppid() if no `claude` ancestor.

        Mirrors clawd_tank_daemon/pid_resolver.py — keep in sync.
        """
        start = os.getppid()
        pid = start
        while pid > 1:
            if _ps("comm", pid) == "claude":
                return pid
            if _CLAUDE_ARGV_RE.search(_ps("command", pid)):
                return pid
            ppid_str = _ps("ppid", pid)
            try:
                pid = int(ppid_str)
            except ValueError:
                break
        return start


    def hook_to_message(hook):
        """Convert a Claude Code hook payload to a daemon message."""
        event_name = hook.get("hook_event_name", "")
        session_id = hook.get("session_id", "")
        cwd = hook.get("cwd", "")
        project = Path(cwd).name if cwd else ""
        pid = _find_claude_pid()

        if event_name == "SessionStart":
            msg = {"event": "session_start", "session_id": session_id, "project": project, "pid": pid}
            source = hook.get("source")
            if source is not None:
                msg["source"] = source
            return msg

        if event_name == "PreToolUse":
            return {"event": "tool_use", "session_id": session_id, "tool_name": hook.get("tool_name", ""), "project": project, "pid": pid}

        if event_name == "PreCompact":
            return {"event": "compact", "session_id": session_id, "pid": pid}

        if event_name == "Stop":
            return {
                "event": "add",
                "hook": "Stop",
                "session_id": session_id,
                "project": project or "unknown",
                "message": "Waiting for input",
                "pid": pid,
            }

        if event_name == "StopFailure":
            message = hook.get("error", "") or hook.get("stop_reason", "") or "API error"
            return {
                "event": "add",
                "hook": "StopFailure",
                "session_id": session_id,
                "project": project or "unknown",
                "message": message,
                "pid": pid,
            }

        if event_name == "Notification":
            if hook.get("notification_type") != "idle_prompt":
                return None
            return {
                "event": "add",
                "hook": "Notification",
                "session_id": session_id,
                "project": project or "unknown",
                "message": hook.get("message", "Waiting for input"),
                "pid": pid,
            }

        if event_name == "UserPromptSubmit":
            return {"event": "dismiss", "hook": "UserPromptSubmit", "session_id": session_id, "pid": pid}

        if event_name == "SessionEnd":
            msg = {"event": "dismiss", "hook": "SessionEnd", "session_id": session_id, "pid": pid}
            reason = hook.get("reason")
            if reason is not None:
                msg["reason"] = reason
            return msg

        if event_name == "SubagentStart":
            return {
                "event": "subagent_start",
                "session_id": session_id,
                "agent_id": hook.get("agent_id", ""),
                "pid": pid,
            }

        if event_name == "SubagentStop":
            return {
                "event": "subagent_stop",
                "session_id": session_id,
                "agent_id": hook.get("agent_id", ""),
                "pid": pid,
            }

        return None


    def main():
        try:
            raw = sys.stdin.read()
            if not raw.strip():
                sys.exit(0)
            payload = json.loads(raw)
        except json.JSONDecodeError:
            sys.exit(1)

        msg = hook_to_message(payload)
        if msg is None:
            sys.exit(0)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(3.0)
            sock.connect(SOCKET_PATH)
            sock.sendall(json.dumps(msg).encode("utf-8") + b"\\n")
        except (ConnectionRefusedError, FileNotFoundError, socket.timeout):
            sys.exit(0)
        finally:
            sock.close()


    if __name__ == "__main__":
        main()
''')
```

Note: The `SOCKET_PATH` line is changed to honor `CLAWD_TANK_SOCKET` env var so the integration test can target a tmp socket. In production, the env var is absent and it defaults to the original path.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_notify_script.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Force script reinstall on next menu bar app launch**

In `host/clawd_tank_menubar/hooks.py`, locate `install_notify_script` (around line 176) and ensure it always overwrites the script file regardless of existing content. It already does (uses `write_text`), so no change needed — but verify by reading the function.

Add a brief version stamp comment at the top of NOTIFY_SCRIPT (after the docstring) so future hash-based update detection has something to compare:

```python
    # NOTIFY_SCRIPT_VERSION: 2026-05-16-pid-tracking
```

This goes inside the triple-quoted string, as a Python comment in the generated script.

- [ ] **Step 6: Commit**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git add host/clawd_tank_menubar/hooks.py host/tests/test_notify_script.py
git commit -m "feat: notify script stamps claude pid and captures source/reason"
```

---

## Task 5: Update `session_store.py` PID handling

**Files:**
- Modify: `host/clawd_tank_daemon/session_store.py`
- Test: `host/tests/test_session_store.py`

- [ ] **Step 1: Write failing tests**

Append to `host/tests/test_session_store.py`:

```python
# --- PID handling (ghost-crab fix) ---


def test_save_persists_pid_field(tmp_path):
    """pid is part of the persisted session state."""
    path = tmp_path / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": 1.0, "pid": 4242}}, path)
    raw = json.loads(path.read_text())
    assert raw["sessions"]["s1"]["pid"] == 4242


def test_load_drops_pid_field(tmp_path):
    """Stored PIDs are unsafe across restart — load returns entries without pid."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "sessions": {"s1": {"state": "idle", "last_event": 1.0, "pid": 4242}},
    }))
    loaded, _, _ = load_sessions(path)
    assert "s1" in loaded
    assert "pid" not in loaded["s1"]


def test_save_excludes_last_event_monotonic(tmp_path):
    """last_event_monotonic is in-memory only; never persisted."""
    path = tmp_path / "sessions.json"
    save_sessions({"s1": {
        "state": "idle", "last_event": 1.0, "last_event_monotonic": 12345.6,
    }}, path)
    raw = json.loads(path.read_text())
    assert "last_event_monotonic" not in raw["sessions"]["s1"]


def test_load_ignores_persisted_monotonic_if_present(tmp_path):
    """Defensive: even if last_event_monotonic somehow got persisted, drop it on load."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "sessions": {"s1": {"state": "idle", "last_event": 1.0, "last_event_monotonic": 99.0}},
    }))
    loaded, _, _ = load_sessions(path)
    assert "last_event_monotonic" not in loaded["s1"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_store.py -v -k "pid or monotonic"
```
Expected: `test_load_drops_pid_field` and `test_save_excludes_last_event_monotonic` FAIL.

- [ ] **Step 3: Update `session_store.py`**

In `host/clawd_tank_daemon/session_store.py`, modify `save_sessions` to exclude `last_event_monotonic`:

```python
def save_sessions(
    sessions: dict[str, dict],
    path: Path = SESSIONS_PATH,
    *,
    order: list[tuple[str, int]] | None = None,
    next_id: int | None = None,
) -> None:
    """Save session states to JSON atomically. Sets are converted to sorted lists.

    `last_event_monotonic` is in-memory only and excluded.
    """
    serializable = {}
    for sid, state in sessions.items():
        entry = {k: v for k, v in state.items() if k != "last_event_monotonic"}
        if "subagents" in entry:
            entry["subagents"] = sorted(entry["subagents"])
        serializable[sid] = entry
    # … rest unchanged
```

Modify `load_sessions` to drop `pid` and `last_event_monotonic` from each loaded entry:

```python
        valid = {}
        for sid, state in raw_sessions.items():
            if not isinstance(state, dict):
                continue
            if "state" not in state or "last_event" not in state:
                continue
            if not isinstance(state["last_event"], (int, float)):
                continue
            # Drop unsafe-across-restart fields. PID may have been recycled;
            # monotonic time is meaningless after process restart.
            state.pop("pid", None)
            state.pop("last_event_monotonic", None)
            if "subagents" in state:
                if not isinstance(state["subagents"], list):
                    del state["subagents"]
                else:
                    state["subagents"] = set(state["subagents"])
            valid[sid] = state
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_store.py -v
```
Expected: all tests PASS, including the existing ones.

- [ ] **Step 5: Commit**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git add host/clawd_tank_daemon/session_store.py host/tests/test_session_store.py
git commit -m "feat: session_store persists pid but drops it on load (recycled PIDs unsafe)"
```

---

## Task 6: Add `pid` and `last_event_monotonic` to daemon session state

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:159-350` (`_handle_message`, `_update_session_state`, `__init__`)
- Test: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests**

Append to `host/tests/test_session_state.py`:

```python
# --- PID + monotonic tracking (ghost-crab fix) ---

@pytest.mark.asyncio
async def test_session_start_stamps_pid_and_monotonic():
    d = make_daemon()
    await d._handle_message({
        "event": "session_start", "session_id": "s1", "pid": 4242,
    })
    assert d._session_states["s1"]["pid"] == 4242
    assert "last_event_monotonic" in d._session_states["s1"]
    assert isinstance(d._session_states["s1"]["last_event_monotonic"], float)


@pytest.mark.asyncio
async def test_tool_use_refreshes_pid_and_monotonic():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "working", "last_event": 1.0,
        "pid": 1111, "last_event_monotonic": 0.0,
    }
    await d._handle_message({
        "event": "tool_use", "session_id": "s1", "tool_name": "Edit", "pid": 4242,
    })
    assert d._session_states["s1"]["pid"] == 4242
    assert d._session_states["s1"]["last_event_monotonic"] > 0.0


@pytest.mark.asyncio
async def test_message_without_pid_field_does_not_crash():
    """Backwards-compat: old notify script sends no pid; daemon must cope."""
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert "s1" in d._session_states
    # pid should be None (or absent) — explicit absence, not error
    assert d._session_states["s1"].get("pid") is None


def test_init_stamps_monotonic_on_loaded_sessions(tmp_path):
    """After daemon restart, loaded sessions get fresh last_event_monotonic."""
    from clawd_tank_daemon.session_store import save_sessions
    sessions_path = tmp_path / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": time.time()}}, sessions_path)

    from clawd_tank_daemon.daemon import ClawdDaemon
    d = ClawdDaemon(sim_only=True, sessions_path=sessions_path)
    d._transports.clear()
    d._transport_queues.clear()

    assert "s1" in d._session_states
    assert "last_event_monotonic" in d._session_states["s1"]
    assert isinstance(d._session_states["s1"]["last_event_monotonic"], float)


def test_init_prunes_wall_clock_stale_sessions(tmp_path):
    """Startup prune: sessions with wall-clock last_event older than 10min
    are removed at init (their Claude Code process is almost certainly dead)."""
    from clawd_tank_daemon.session_store import save_sessions
    sessions_path = tmp_path / "sessions.json"
    save_sessions(
        {
            "fresh": {"state": "idle", "last_event": time.time()},
            "stale": {"state": "idle", "last_event": time.time() - 3600},  # 1h ago
        },
        sessions_path,
        order=[("fresh", 1), ("stale", 2)],
        next_id=3,
    )

    from clawd_tank_daemon.daemon import ClawdDaemon
    d = ClawdDaemon(sim_only=True, sessions_path=sessions_path)
    d._transports.clear()
    d._transport_queues.clear()

    assert "fresh" in d._session_states
    assert "stale" not in d._session_states
    assert d._session_order == [("fresh", 1)]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_state.py -v -k "pid or monotonic"
```
Expected: all 4 new tests FAIL.

- [ ] **Step 3: Update `daemon.py` — `__init__`**

In `ClawdDaemon.__init__` (around line 150), after the existing `loaded_states, loaded_order, loaded_next_id = load_sessions(...)` line, do a one-shot wall-clock prune (sessions whose wall-clock `last_event` is older than the staleness timeout almost certainly belong to dead Claude Code processes), THEN stamp fresh monotonic time on the survivors:

```python
        loaded_states, loaded_order, loaded_next_id = load_sessions(self._sessions_path)

        # One-shot startup prune by wall-clock: if last_event is older than the
        # staleness timeout, the session almost certainly belongs to a dead Claude
        # Code process from before the daemon restarted. After this prune we switch
        # to monotonic time for runtime tracking (survives macOS sleep/wake).
        now_wall = time.time()
        stale_ids = [
            sid for sid, s in loaded_states.items()
            if now_wall - s.get("last_event", now_wall) > 600.0  # default timeout
        ]
        for sid in stale_ids:
            del loaded_states[sid]
        loaded_order = [(sid, did) for sid, did in loaded_order if sid not in stale_ids]

        now_mono = time.monotonic()
        for state in loaded_states.values():
            state["last_event_monotonic"] = now_mono
        self._session_states: dict[str, dict] = loaded_states
```

(The `600.0` hard-coded here matches the default `_session_staleness_timeout`; it must run before `self._session_staleness_timeout` is assigned. If `set_session_timeout` was called and persisted somewhere, this would need adjustment — currently it isn't, so the hardcode is fine.)

- [ ] **Step 4: Update `_update_session_state` to stamp pid and monotonic**

In `_update_session_state` (around line 285), change the signature to accept `pid`:

```python
    def _update_session_state(
        self, event: str, hook: str, session_id: str,
        agent_id: str = "", tool_name: str = "", pid: Optional[int] = None,
    ) -> bool:
```

At the top of the function (after the `if not session_id: return False`), capture monotonic time:

```python
        now = time.time()
        now_mono = time.monotonic()
```

After the event-specific branches finish (where the function currently appends to `_session_order`), stamp pid and monotonic on the current state IF the session exists:

```python
        # Track session order — append on first appearance
        cur = self._session_states.get(session_id)
        if cur is not None and session_id not in [sid for sid, _ in self._session_order]:
            self._session_order.append((session_id, self._next_display_id))
            self._next_display_id += 1

        # Stamp PID + monotonic on every event (only if session still exists —
        # SessionEnd may have just removed it).
        if cur is not None:
            if pid is not None:
                cur["pid"] = pid
            cur["last_event_monotonic"] = now_mono

        if cur is None:
            return prev is not None  # session was removed
        return cur["state"] != prev_state or cur.get("subagents", set()) != (prev_subagents or set())
```

- [ ] **Step 5: Pass pid through `_handle_message`**

In `_handle_message` (around line 194), pass `pid` to `_update_session_state`:

```python
        changed = self._update_session_state(
            event, hook, session_id,
            msg.get("agent_id", ""), msg.get("tool_name", ""),
            pid=msg.get("pid"),
        )
```

- [ ] **Step 6: Run tests**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_state.py -v
```
Expected: new tests PASS, existing tests still PASS.

- [ ] **Step 7: Commit**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat: track pid and monotonic event time per session"
```

---

## Task 7: Switch staleness eviction to `time.monotonic()`

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:352-365` (`_evict_stale_sessions`)
- Test: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing test**

Append to `host/tests/test_session_state.py`:

```python
def test_staleness_uses_monotonic_not_wall_clock():
    """A session with old wall-clock last_event but fresh monotonic time
    should NOT be evicted — covers macOS sleep/wake scenario."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,            # ancient wall clock
        "last_event_monotonic": time.monotonic(),    # but fresh monotonic
    }
    d._session_staleness_timeout = 600
    d._evict_stale_sessions()
    assert "s1" in d._session_states, "monotonic-fresh session evicted incorrectly"


def test_staleness_evicts_when_monotonic_old():
    """Session with old monotonic time is evicted regardless of wall clock."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic() - 9999,
    }
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    assert "s1" not in d._session_states
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_state.py -v -k "monotonic_not_wall_clock or evicts_when_monotonic"
```
Expected: `test_staleness_uses_monotonic_not_wall_clock` FAILS (current code uses `last_event` wall-clock, evicts the session because wall-clock is ancient).

- [ ] **Step 3: Update `_evict_stale_sessions`**

In `host/clawd_tank_daemon/daemon.py:352`, replace the function body:

```python
    def _evict_stale_sessions(self) -> None:
        # Active subagents refresh last_event via PreToolUse on the parent session.
        # If last_event_monotonic is stale, subagents are dead too — safe to evict.
        # Uses monotonic time so macOS sleep doesn't trigger mass-eviction on wake.
        now_mono = time.monotonic()
        stale = [
            sid for sid, s in self._session_states.items()
            if now_mono - s.get("last_event_monotonic", now_mono) > self._session_staleness_timeout
        ]
        for sid in stale:
            logger.info("Evicting stale session: %s", sid[:12])
            del self._session_states[sid]
        if stale:
            self._session_order = [(sid, did) for sid, did in self._session_order if sid not in stale]
            self._persist_sessions()
```

The `.get("last_event_monotonic", now_mono)` default means sessions without the field (shouldn't happen post-init, but defensive) are treated as fresh — won't be wrongly evicted.

- [ ] **Step 3.5: Remove redundant `_evict_stale_sessions()` call from `__init__`**

In `__init__` (around line 157), remove the `self._evict_stale_sessions()` line. The Task 6 startup wall-clock prune already handled the "old sessions on load" case, and calling the new monotonic-based `_evict_stale_sessions` against freshly-stamped sessions would be a no-op.

```python
        self._session_staleness_timeout: float = 600.0
        # _evict_stale_sessions() removed — Task 6 startup prune covers this.
```

- [ ] **Step 4: Update existing tests that set `last_event` only**

The existing test `test_staleness_evicts_old_sessions` (around line 179) and any others that set `last_event` without `last_event_monotonic` will now pass through (no eviction) — they're testing the old behavior. Update them to also set `last_event_monotonic`:

```python
def test_staleness_evicts_old_sessions():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,
        "last_event_monotonic": time.monotonic() - 9999,
    }
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    assert "s1" not in d._session_states

def test_staleness_keeps_fresh_sessions():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),
    }
    d._session_staleness_timeout = 600
    d._evict_stale_sessions()
    assert "s1" in d._session_states
```

Grep for other tests with `last_event` direct assignment and add `last_event_monotonic` alongside:

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && grep -n "last_event\":" tests/test_session_state.py
```

For each match, ensure the dict includes `"last_event_monotonic": time.monotonic()` (or a relative offset matching `last_event`).

- [ ] **Step 5: Run all session state tests**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_state.py -v
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat: staleness eviction uses monotonic time to survive sleep/wake"
```

---

## Task 8: PID-based dedup on `SessionStart`

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:159-242` (`_handle_message`)
- Test: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests**

Append to `host/tests/test_session_state.py`:

```python
# --- PID-based /clear dedup (ghost-crab fix) ---

@pytest.mark.asyncio
async def test_session_start_evicts_prior_session_with_same_pid_recent():
    """SessionStart with PID that matches a recently-active session = /clear case.
    Old session is evicted in the same _handle_message call."""
    d = make_daemon()
    d._session_states["old"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),  # very recent
        "pid": 4242,
    }
    d._session_order = [("old", 1)]
    d._next_display_id = 2

    await d._handle_message({"event": "session_start", "session_id": "new", "pid": 4242})

    assert "old" not in d._session_states, "/clear dedup did not evict old session"
    assert "new" in d._session_states
    assert d._session_order == [("new", 2)], "session_order not scrubbed/updated"


@pytest.mark.asyncio
async def test_session_start_does_NOT_evict_stale_session_with_same_pid():
    """If the matching session is >60s old (monotonic), assume PID recycle, no eviction."""
    d = make_daemon()
    d._session_states["old"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic() - 120,  # 2 min old
        "pid": 4242,
    }
    d._session_order = [("old", 1)]
    d._next_display_id = 2

    await d._handle_message({"event": "session_start", "session_id": "new", "pid": 4242})

    assert "old" in d._session_states, "PID-recycle dedup wrongly evicted stale session"
    assert "new" in d._session_states


@pytest.mark.asyncio
async def test_session_start_without_pid_does_not_dedup():
    """Backwards-compat: old notify script sends no pid; dedup is skipped safely."""
    d = make_daemon()
    d._session_states["old"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),
        "pid": 4242,
    }
    d._session_order = [("old", 1)]
    d._next_display_id = 2

    await d._handle_message({"event": "session_start", "session_id": "new"})  # no pid

    assert "old" in d._session_states
    assert "new" in d._session_states


@pytest.mark.asyncio
async def test_dedup_scrubs_active_notifications():
    """Evicted session's notification card is also removed."""
    d = make_daemon()
    d._session_states["old"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),
        "pid": 4242,
    }
    d._active_notifications["old"] = {"event": "add", "session_id": "old"}
    d._session_order = [("old", 1)]
    d._next_display_id = 2

    await d._handle_message({"event": "session_start", "session_id": "new", "pid": 4242})

    assert "old" not in d._active_notifications


@pytest.mark.asyncio
async def test_dedup_only_fires_on_session_start():
    """A tool_use event with matching PID does NOT trigger dedup."""
    d = make_daemon()
    d._session_states["old"] = {
        "state": "idle",
        "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),
        "pid": 4242,
    }
    d._session_order = [("old", 1)]
    d._next_display_id = 2

    await d._handle_message({"event": "tool_use", "session_id": "new", "pid": 4242, "tool_name": "Edit"})

    assert "old" in d._session_states
    assert "new" in d._session_states
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_state.py -v -k "dedup or same_pid"
```
Expected: all 5 new tests FAIL (no dedup logic yet — `old` still in `_session_states`).

- [ ] **Step 3: Add dedup logic in `_handle_message`**

In `host/clawd_tank_daemon/daemon.py`, at the start of `_handle_message` (right after computing `extra` and before the existing `if event == "add":` block at line 189), add a constant near the top of the module:

```python
PID_DEDUP_FRESHNESS_SECONDS = 60.0
```

Then inside `_handle_message`, before the `_update_session_state` call (around line 194):

```python
        # --- PID-based dedup: SessionStart with PID matching a recent session = /clear ---
        if event == "session_start":
            incoming_pid = msg.get("pid")
            if incoming_pid is not None:
                now_mono = time.monotonic()
                to_evict = []
                for sid, state in self._session_states.items():
                    if sid == session_id:
                        continue
                    if state.get("pid") != incoming_pid:
                        continue
                    last_mono = state.get("last_event_monotonic", now_mono)
                    if now_mono - last_mono < PID_DEDUP_FRESHNESS_SECONDS:
                        to_evict.append(sid)
                for sid in to_evict:
                    logger.info(
                        "PID dedup: evicting session %s (PID %d reused by new session %s)",
                        sid[:12], incoming_pid, session_id[:12],
                    )
                    del self._session_states[sid]
                    self._session_order = [(s, d) for s, d in self._session_order if s != sid]
                    self._active_notifications.pop(sid, None)
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_state.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat: PID-based dedup on SessionStart handles /clear without depending on SessionEnd"
```

---

## Task 9: Liveness polling task

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py` (add `_liveness_checker`, wire in `run()`)
- Test: `host/tests/test_session_state.py`

- [ ] **Step 1: Write failing tests**

Append to `host/tests/test_session_state.py`:

```python
# --- Liveness polling (ghost-crab fix) ---

def test_liveness_evicts_dead_pid():
    """Session whose stored PID raises ProcessLookupError on kill(pid, 0) is evicted."""
    from unittest.mock import patch
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(), "pid": 4242,
    }
    d._session_order = [("s1", 1)]

    with patch("clawd_tank_daemon.daemon.os.kill", side_effect=ProcessLookupError):
        d._check_liveness()

    assert "s1" not in d._session_states
    assert d._session_order == []


def test_liveness_keeps_alive_pid():
    """Session whose PID is alive (kill returns) stays."""
    from unittest.mock import patch
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(), "pid": 4242,
    }

    with patch("clawd_tank_daemon.daemon.os.kill", return_value=None):
        d._check_liveness()

    assert "s1" in d._session_states


def test_liveness_skips_sessions_without_pid():
    """No-pid sessions (e.g. post-restart, pre-first-event) are skipped, not evicted."""
    from unittest.mock import patch
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(),
    }

    with patch("clawd_tank_daemon.daemon.os.kill", side_effect=ProcessLookupError):
        d._check_liveness()

    assert "s1" in d._session_states


def test_liveness_treats_permission_error_as_alive():
    """If kill raises PermissionError (PID belongs to another user), assume alive."""
    from unittest.mock import patch
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(), "pid": 4242,
    }

    with patch("clawd_tank_daemon.daemon.os.kill", side_effect=PermissionError):
        d._check_liveness()

    assert "s1" in d._session_states


def test_liveness_persists_after_eviction(tmp_path):
    """After evicting a dead session, the persisted sessions.json reflects it."""
    from unittest.mock import patch
    from clawd_tank_daemon.daemon import ClawdDaemon

    d = ClawdDaemon(sim_only=True, sessions_path=tmp_path / "sessions.json")
    d._transports.clear()
    d._transport_queues.clear()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(),
        "last_event_monotonic": time.monotonic(), "pid": 4242,
    }
    d._session_order = [("s1", 1)]

    with patch("clawd_tank_daemon.daemon.os.kill", side_effect=ProcessLookupError):
        d._check_liveness()

    # Persist file should not contain s1
    import json as _json
    raw = _json.loads((tmp_path / "sessions.json").read_text())
    assert "s1" not in raw.get("sessions", {})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_state.py -v -k "liveness"
```
Expected: all 5 new tests FAIL (`_check_liveness` not defined).

- [ ] **Step 3: Implement `_check_liveness` and `_liveness_checker`**

In `host/clawd_tank_daemon/daemon.py`, add after `_evict_stale_sessions` (around line 366):

```python
    def _check_liveness(self) -> list[str]:
        """Synchronous half of the liveness check — separated for testability.

        Returns the list of evicted session_ids.
        """
        dead = []
        for sid, state in self._session_states.items():
            pid = state.get("pid")
            if pid is None:
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                dead.append(sid)
            except PermissionError:
                pass  # PID belongs to another user — assume alive
        for sid in dead:
            logger.info(
                "Liveness: evicting session %s (PID %d gone)",
                sid[:12], self._session_states[sid].get("pid"),
            )
            del self._session_states[sid]
            self._session_order = [(s, d) for s, d in self._session_order if s != sid]
            self._active_notifications.pop(sid, None)
        if dead:
            self._persist_sessions()
        return dead

    async def _liveness_checker(self) -> None:
        """Async task: every 30s, evict sessions whose Claude Code PID is gone."""
        while self._running:
            await asyncio.sleep(30)
            evicted = self._check_liveness()
            if evicted:
                await self._broadcast_display_state_if_changed()
```

- [ ] **Step 4: Wire `_liveness_checker` into `run()`**

In `run()` (around line 627), after the existing `self._staleness_task = asyncio.create_task(self._staleness_checker())` line, add:

```python
        self._staleness_task = asyncio.create_task(self._staleness_checker())
        self._liveness_task = asyncio.create_task(self._liveness_checker())
```

In `_shutdown()` (around line 519), after the existing `_staleness_task` cleanup block, add the mirror for `_liveness_task`:

```python
        if hasattr(self, '_liveness_task'):
            self._liveness_task.cancel()
            try:
                await self._liveness_task
            except asyncio.CancelledError:
                pass
```

- [ ] **Step 5: Run tests**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest tests/test_session_state.py -v
```
Expected: all liveness tests PASS, all previous tests still PASS.

- [ ] **Step 6: Commit**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git add host/clawd_tank_daemon/daemon.py host/tests/test_session_state.py
git commit -m "feat: liveness polling task evicts sessions whose Claude PID is dead"
```

---

## Task 10: Wire source/reason logging

**Files:**
- Modify: `host/clawd_tank_daemon/daemon.py:159-188` (`_handle_message` logging)

- [ ] **Step 1: Add source/reason to the existing log line**

In `_handle_message` (around line 186), the existing `logger.info("Socket msg: …")` builds an `extra` string. Add source/reason to it:

```python
        extra = ""
        if event == "tool_use":
            extra = f" tool={msg.get('tool_name', '?')}"
        elif event in ("subagent_start", "subagent_stop"):
            extra = f" agent={msg.get('agent_id', '?')[:12]}"
        elif event == "add":
            extra = f" msg={msg.get('message', '')[:30]}"
        if msg.get("source"):
            extra += f" source={msg['source']}"
        if msg.get("reason"):
            extra += f" reason={msg['reason']}"
        if msg.get("pid"):
            extra += f" pid={msg['pid']}"
```

- [ ] **Step 2: Verify by running full test suite**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest -v
```
Expected: all tests PASS. No new tests for logging (logging changes are observable in real-world usage; unit tests would just mirror the code).

- [ ] **Step 3: Commit**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git add host/clawd_tank_daemon/daemon.py
git commit -m "feat: log pid, source, reason fields for ghost-crab debugging"
```

---

## Task 11: Manual end-to-end verification

**Files:** none (manual checkpoint)

- [ ] **Step 1: Build and install the menu bar app**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && ./build.sh --install
```

- [ ] **Step 2: Kill running app and relaunch**

```bash
pkill -f "Clawd Tank" || true
open -a "Clawd Tank"
```

Wait for the menu bar icon to appear. Confirm it connects to a transport (BLE or Simulator).

- [ ] **Step 3: Verify SIGKILL eviction**

1. Open a new terminal, run `claude`.
2. Confirm a new crab appears on screen.
3. Force-quit the terminal (close window, or `kill -9` the process).
4. Wait ≤30s. Crab should disappear (liveness eviction).
5. Inspect log: `tail -100 ~/Library/Logs/ClawdTank/clawd-tank.log` should show `Liveness: evicting session …`.

- [ ] **Step 4: Verify `/clear` dedup**

1. Open a terminal, run `claude`.
2. Send a prompt to register the session.
3. Run `/clear` in Claude Code.
4. Confirm: the crab does NOT duplicate; the existing crab is replaced (or evicted and a new one walks in).
5. Inspect log: should show `PID dedup: evicting session …`.

- [ ] **Step 5: Verify `--resume` does NOT cause duplicates**

1. In a terminal, run `claude` and send a prompt.
2. `/exit` (clean exit fires SessionEnd).
3. Run `claude --continue`. Confirm: one crab, not two.

- [ ] **Step 6: Verify subagents don't corrupt PID tracking**

1. Run `claude`, ask it to dispatch a subagent (e.g., `Use the Explore agent to find...`).
2. Wait for subagent to finish, then keep the parent session alive.
3. Confirm: parent crab is NOT evicted when subagent process exits.

- [ ] **Step 7: Full test suite**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank/host && .venv/bin/pytest -v
```
Expected: 100% PASS.

- [ ] **Step 8: Final cleanup commit (if any leftover changes)**

```bash
cd /Users/marciorodrigues/Projects/clawd-tank
git status
# If clean, no commit needed. If there are stragglers from manual edits:
git diff
git add -p
git commit -m "chore: ghost-crab fix manual verification cleanup"
```

---

## Self-Review Notes

- **Spec coverage**: every numbered item in `2026-05-16-ghost-crab-fix-design.md` "Design Decisions" table maps to a task (PID resolution → Task 2/4; liveness primitive → Task 9; PID stamping frequency → Task 4/6; dedup freshness window → Task 8; sessions.json on load → Task 5; staleness timeout kept at 10min → no change needed, current default in `daemon.py:156` already 600.0; source/reason capture → Tasks 3/4/10; no new deps → confirmed throughout).
- **Empirical verification**: Task 1 covers the spec's "Empirical Verification Step" before any code lands.
- **Staleness timeout (10min)**: spec keeps the existing default — no task needed since it's already `600.0` in `daemon.py:156`.
- **Migration / compat**: covered implicitly by the "backwards-compat" tests in Tasks 3 (`test_session_start_missing_pid_field_omits_it`) and 8 (`test_session_start_without_pid_does_not_dedup`).
