# host/clawd_tank_menubar/hooks.py
"""Install the Claude Code hook script and configure hooks in settings."""

import json
import logging
import os
import stat
import textwrap
from pathlib import Path

logger = logging.getLogger("clawd-tank.hooks")

CLAWD_DIR = Path.home() / ".clawd-tank"
NOTIFY_SCRIPT_PATH = CLAWD_DIR / "clawd-tank-notify"
CLAUDE_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# Standalone hook script — uses only Python stdlib, no external imports.
NOTIFY_SCRIPT = textwrap.dedent('''\
    #!/usr/bin/env python3
    """clawd-tank-notify - Claude Code hook handler for Clawd Tank.

    Reads hook payload from stdin, converts it to a daemon message,
    and forwards it via Unix socket. No external dependencies.
    """
    # NOTIFY_SCRIPT_VERSION: 2026-05-16-pid-tracking

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

HOOK_COMMAND = str(NOTIFY_SCRIPT_PATH)

HOOKS_CONFIG = {
    "SessionStart": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
    "Stop": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
    "StopFailure": [
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
    "SubagentStart": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
    "SubagentStop": [
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ],
}


def install_notify_script() -> None:
    """Write the standalone notify script to ~/.clawd-tank/clawd-tank-notify."""
    CLAWD_DIR.mkdir(parents=True, exist_ok=True)
    NOTIFY_SCRIPT_PATH.write_text(NOTIFY_SCRIPT, encoding="utf-8")
    NOTIFY_SCRIPT_PATH.chmod(0o755)
    logger.info("Installed hook script: %s", NOTIFY_SCRIPT_PATH)


def are_hooks_installed() -> bool:
    """Check if Claude Code settings have all required Clawd Tank hooks."""
    if not CLAUDE_SETTINGS_PATH.exists():
        return False
    try:
        settings = json.loads(CLAUDE_SETTINGS_PATH.read_text())
        hooks = settings.get("hooks", {})
        # Every hook event in HOOKS_CONFIG must be present and point to our script
        for event_name in HOOKS_CONFIG:
            if event_name not in hooks:
                return False
            found = False
            for entry in hooks[event_name]:
                for h in entry.get("hooks", []):
                    if HOOK_COMMAND in h.get("command", ""):
                        found = True
                        break
            if not found:
                return False
        return True
    except (json.JSONDecodeError, OSError):
        pass
    return False


def install_hooks() -> bool:
    """Add Clawd Tank hooks to Claude Code settings. Returns True on success."""
    CLAUDE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)

    if CLAUDE_SETTINGS_PATH.exists():
        try:
            settings = json.loads(CLAUDE_SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}
    else:
        settings = {}

    settings.setdefault("hooks", {})
    settings["hooks"].update(HOOKS_CONFIG)

    CLAUDE_SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n")
    logger.info("Installed hooks in %s", CLAUDE_SETTINGS_PATH)
    return True
