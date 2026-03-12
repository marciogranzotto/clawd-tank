"""Message format conversion between Claude Code hooks, daemon, and BLE."""

import json
from pathlib import Path
from typing import Optional


def hook_payload_to_daemon_message(hook: dict) -> Optional[dict]:
    """Convert a Claude Code hook stdin payload to a daemon message.

    Returns None if the hook event is not relevant (should be ignored).
    """
    event_name = hook.get("hook_event_name", "")
    session_id = hook.get("session_id", "")

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
            "session_id": session_id,
            "project": project,
            "message": message,
        }

    if event_name in ("UserPromptSubmit", "SessionEnd"):
        return {
            "event": "dismiss",
            "session_id": session_id,
        }

    return None


def daemon_message_to_ble_payload(msg: dict) -> str:
    """Convert a daemon message to a JSON string for BLE GATT write."""
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

    raise ValueError(f"Unknown event: {event}")
