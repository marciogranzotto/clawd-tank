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


def _make_sock_path() -> str:
    """Return a short Unix socket path safe on macOS (max 104 bytes).

    pytest's tmp_path on macOS expands to a very long path under
    /private/var/folders/... which exceeds AF_UNIX's 104-byte limit.
    Using tempfile.mkstemp in /tmp gives a short, safe path.
    """
    fd, path = tempfile.mkstemp(prefix="ct_", suffix=".sock", dir="/tmp")
    os.close(fd)
    os.unlink(path)  # bind() requires the file to not exist
    return path


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
            try:
                os.unlink(sock_path)
            except FileNotFoundError:
                pass

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
    sock_path = _make_sock_path()
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
    sock_path = _make_sock_path()
    msg = _run_script_with_payload({
        "hook_event_name": "Stop",
        "session_id": "s2",
        "cwd": str(tmp_path),
    }, sock_path)
    assert msg.get("event") == "add"
    assert msg.get("hook") == "Stop"
    assert isinstance(msg.get("pid"), int)


def test_notify_script_session_end_includes_reason(tmp_path):
    sock_path = _make_sock_path()
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
    sock_path = _make_sock_path()
    msg = _run_script_with_payload({
        "hook_event_name": "SomeUnhandledEvent",  # not handled
        "session_id": "s4",
    }, sock_path)
    assert msg == {}


def test_notify_script_post_tool_use_produces_tool_done(tmp_path):
    sock_path = _make_sock_path()
    msg = _run_script_with_payload({
        "hook_event_name": "PostToolUse",
        "session_id": "s5",
        "tool_name": "AskUserQuestion",
        "cwd": str(tmp_path),
    }, sock_path)
    assert msg.get("event") == "tool_done"
    assert msg.get("session_id") == "s5"
    assert msg.get("tool_name") == "AskUserQuestion"
    assert isinstance(msg.get("pid"), int)


def test_notify_script_permission_request_produces_permission(tmp_path):
    sock_path = _make_sock_path()
    msg = _run_script_with_payload({
        "hook_event_name": "PermissionRequest",
        "session_id": "s6",
        "tool_name": "Bash",
        "cwd": str(tmp_path),
    }, sock_path)
    assert msg.get("event") == "permission"
    assert msg.get("session_id") == "s6"
    assert msg.get("tool_name") == "Bash"
    assert isinstance(msg.get("pid"), int)


def test_notify_script_post_tool_use_failure_produces_tool_failed(tmp_path):
    sock_path = _make_sock_path()
    msg = _run_script_with_payload({
        "hook_event_name": "PostToolUseFailure",
        "session_id": "s7",
        "tool_name": "Read",
        "cwd": str(tmp_path),
    }, sock_path)
    assert msg.get("event") == "tool_failed"
    assert msg.get("session_id") == "s7"
    assert msg.get("tool_name") == "Read"
    assert isinstance(msg.get("pid"), int)
