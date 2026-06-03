#!/usr/bin/env python
"""
clawd-tank-hook.py — Windows-compatible Claude Code hook handler.
Reads hook events from stdin and sends TCP commands directly to the simulator.
No daemon needed — this script talks TCP to clawd-tank-sim.exe on port 19872.
"""
import sys
import json
import socket
import os
import time

TCP_HOST = "127.0.0.1"
TCP_PORT = 19872

# Tool name -> animation mapping (from upstream daemon.py)
TOOL_ANIMATION_MAP = {
    "Edit": "typing", "Write": "typing", "NotebookEdit": "typing",
    "Read": "debugger", "Grep": "debugger", "Glob": "debugger",
    "Bash": "building",
    "Agent": "conducting",
    "WebSearch": "wizard", "WebFetch": "wizard",
    "LSP": "beacon",
}

# State file to persist session info across hook invocations
STATE_FILE = os.path.join(os.path.expanduser("~"), ".clawd-tank", "win-state.json")


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"sessions": {}, "next_display_id": 1}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_tcp(messages):
    """Send one or more JSON messages to the simulator via TCP."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        sock.connect((TCP_HOST, TCP_PORT))
        for msg in messages:
            line = json.dumps(msg) + "\n"
            sock.sendall(line.encode())
        sock.close()
    except Exception:
        pass  # Simulator not running, silently ignore


def get_display_id(state, session_id):
    """Get or assign a stable display ID for a session."""
    if session_id not in state["sessions"]:
        state["sessions"][session_id] = {
            "display_id": state["next_display_id"],
            "anim": "idle",
            "last_seen": time.time(),
            "subagents": 0,
        }
        state["next_display_id"] += 1
    state["sessions"][session_id]["last_seen"] = time.time()
    return state["sessions"][session_id]["display_id"]


def evict_stale(state, timeout=600):
    """Remove sessions not seen in the last timeout seconds."""
    now = time.time()
    stale = [sid for sid, s in state["sessions"].items()
             if now - s["last_seen"] > timeout]
    for sid in stale:
        del state["sessions"][sid]


def build_set_sessions(state):
    """Build a set_sessions TCP message from current state."""
    sessions = list(state["sessions"].values())
    if not sessions:
        return {"action": "set_status", "status": "sleeping"}

    visible = sessions[:4]
    overflow = max(0, len(sessions) - 4)
    total_subagents = sum(s.get("subagents", 0) for s in sessions)

    return {
        "action": "set_sessions",
        "anims": [s["anim"] for s in visible],
        "ids": [s["display_id"] for s in visible],
        "subagents": total_subagents,
        "overflow": overflow,
    }


def main():
    # Read hook payload from stdin
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        payload = json.loads(raw)
    except Exception:
        return

    hook_name = payload.get("hook_event_name", "")
    session_id = payload.get("session_id", "unknown")
    project = payload.get("cwd", "")
    if project:
        project = os.path.basename(project)

    state = load_state()
    evict_stale(state)
    messages = []

    if hook_name == "SessionStart":
        get_display_id(state, session_id)
        state["sessions"][session_id]["anim"] = "idle"

    elif hook_name == "PreToolUse":
        tool_name = payload.get("tool_name", "")
        anim = TOOL_ANIMATION_MAP.get(tool_name, "typing")
        # Check for MCP tools
        if tool_name.startswith("mcp__"):
            anim = "beacon"
        get_display_id(state, session_id)
        state["sessions"][session_id]["anim"] = anim

    elif hook_name == "PreCompact":
        if session_id in state["sessions"]:
            state["sessions"][session_id]["anim"] = "sweeping"

    elif hook_name == "Stop":
        if session_id in state["sessions"]:
            state["sessions"][session_id]["anim"] = "idle"
        # Add notification card
        messages.append({
            "action": "add",
            "id": session_id,
            "project": project or "claude",
            "message": "Waiting for input",
        })

    elif hook_name == "StopFailure":
        if session_id in state["sessions"]:
            state["sessions"][session_id]["anim"] = "dizzy"
        messages.append({
            "action": "add",
            "id": session_id,
            "project": project or "claude",
            "message": "API error",
            "alert": "error",
        })

    elif hook_name == "Notification":
        notif_type = payload.get("notification_type", "")
        if notif_type == "idle_prompt":
            if session_id in state["sessions"]:
                state["sessions"][session_id]["anim"] = "confused"
            messages.append({
                "action": "add",
                "id": session_id,
                "project": project or "claude",
                "message": payload.get("message", "Waiting for input"),
            })

    elif hook_name == "UserPromptSubmit":
        if session_id in state["sessions"]:
            state["sessions"][session_id]["anim"] = "thinking"
        messages.append({"action": "dismiss", "id": session_id})

    elif hook_name == "SessionEnd":
        messages.append({"action": "dismiss", "id": session_id})
        if session_id in state["sessions"]:
            del state["sessions"][session_id]

    elif hook_name == "SubagentStart":
        if session_id in state["sessions"]:
            state["sessions"][session_id]["subagents"] = \
                state["sessions"][session_id].get("subagents", 0) + 1

    elif hook_name == "SubagentStop":
        if session_id in state["sessions"]:
            state["sessions"][session_id]["subagents"] = \
                max(0, state["sessions"][session_id].get("subagents", 0) - 1)

    else:
        return  # Unknown hook, ignore

    # Build the display state message
    messages.append(build_set_sessions(state))

    # Send time sync
    messages.insert(0, {
        "action": "set_time",
        "epoch": int(time.time()),
    })

    save_state(state)
    send_tcp(messages)


if __name__ == "__main__":
    main()
