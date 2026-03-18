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

    import json
    import socket
    import sys
    from pathlib import Path

    SOCKET_PATH = str(Path.home() / ".clawd-tank" / "sock")


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

        if event_name == "StopFailure":
            cwd = hook.get("cwd", "")
            project = Path(cwd).name if cwd else "unknown"
            message = hook.get("error", "") or hook.get("stop_reason", "") or "API error"
            return {
                "event": "add",
                "hook": "StopFailure",
                "session_id": session_id,
                "project": project or "unknown",
                "message": message,
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
            return {"event": "dismiss", "hook": "UserPromptSubmit", "session_id": session_id}

        if event_name == "SessionEnd":
            return {"event": "dismiss", "hook": "SessionEnd", "session_id": session_id}

        if event_name == "SubagentStart":
            return {
                "event": "subagent_start",
                "session_id": session_id,
                "agent_id": hook.get("agent_id", ""),
            }

        if event_name == "SubagentStop":
            return {
                "event": "subagent_stop",
                "session_id": session_id,
                "agent_id": hook.get("agent_id", ""),
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
