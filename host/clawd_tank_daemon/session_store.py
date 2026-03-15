"""Persist daemon session state to disk for restart recovery."""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("clawd-tank")

SESSIONS_PATH = Path.home() / ".clawd-tank" / "sessions.json"


def save_sessions(sessions: dict[str, dict], path: Path = SESSIONS_PATH) -> None:
    """Save session states to JSON atomically. Sets are converted to sorted lists."""
    serializable = {}
    for sid, state in sessions.items():
        entry = {**state}
        if "subagents" in entry:
            entry["subagents"] = sorted(entry["subagents"])
        serializable[sid] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(serializable, f)
        os.replace(tmp_path, str(path))
    except OSError:
        logger.warning("Failed to save session state to %s", path)
        try:
            os.unlink(tmp_path)
        except (OSError, UnboundLocalError):
            pass


def load_sessions(path: Path = SESSIONS_PATH) -> dict[str, dict]:
    """Load session states from JSON. Returns empty dict on any error.

    Entries missing required keys (state, last_event) are silently dropped.
    """
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}
        valid = {}
        for sid, state in data.items():
            if not isinstance(state, dict):
                continue
            if "state" not in state or "last_event" not in state:
                continue
            if not isinstance(state["last_event"], (int, float)):
                continue
            if "subagents" in state:
                if not isinstance(state["subagents"], list):
                    del state["subagents"]
                else:
                    state["subagents"] = set(state["subagents"])
            valid[sid] = state
        return valid
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return {}
