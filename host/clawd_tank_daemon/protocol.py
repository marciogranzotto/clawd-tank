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

    if event_name == "SessionStart":
        return {
            "event": "session_start",
            "session_id": session_id,
        }

    if event_name == "PreToolUse":
        return {
            "event": "tool_use",
            "session_id": session_id,
        }

    if event_name == "PreCompact":
        return {
            "event": "compact",
            "session_id": session_id,
        }

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

    if event_name == "StopFailure":
        cwd = hook.get("cwd", "")
        project = Path(cwd).name if cwd else "unknown"
        if not project:
            project = "unknown"
        message = hook.get("error", "") or hook.get("stop_reason", "") or "API error"
        return {
            "event": "add",
            "hook": "StopFailure",
            "session_id": session_id,
            "project": project,
            "message": message,
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


def daemon_message_to_ble_payload(msg: dict) -> Optional[str]:
    """Convert a daemon message to a JSON string for BLE GATT write.

    Returns None for session-internal events (session_start, tool_use, compact,
    subagent_start, subagent_stop) that have no BLE representation.
    Raises ValueError for unknown events.
    """
    event = msg["event"]

    if event in ("session_start", "tool_use", "compact", "subagent_start", "subagent_stop"):
        return None

    if event == "add":
        payload = {
            "action": "add",
            "id": msg.get("session_id", ""),
            "project": msg.get("project", ""),
            "message": msg.get("message", ""),
        }
        if msg.get("hook") == "StopFailure":
            payload["alert"] = "error"
        return json.dumps(payload)

    if event == "dismiss":
        return json.dumps({
            "action": "dismiss",
            "id": msg.get("session_id", ""),
        })

    if event == "clear":
        return json.dumps({"action": "clear"})

    raise ValueError(f"Unknown event: {event}")


def display_state_to_ble_payload(state: dict) -> str:
    """Convert display state dict to v2 JSON payload."""
    if "status" in state:
        return json.dumps({"action": "set_status", "status": state["status"]})
    payload = {"action": "set_sessions", **state}
    return json.dumps(payload)


def display_state_to_v1_payload(state: dict) -> str:
    """Convert display state dict to legacy v1 set_status payload."""
    if "status" in state:
        return json.dumps({"action": "set_status", "status": state["status"]})
    working = sum(1 for a in state.get("anims", []) if a in ("typing", "building"))
    if working > 0:
        status = f"working_{min(working, 3)}"
    elif "thinking" in state.get("anims", []):
        status = "thinking"
    elif any(a in ("confused", "dizzy") for a in state.get("anims", [])):
        status = "confused"
    else:
        status = "idle"
    return json.dumps({"action": "set_status", "status": status})
