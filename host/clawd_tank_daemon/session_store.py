"""Persist daemon session state to disk for restart recovery."""

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("clawd-tank")

SESSIONS_PATH = Path.home() / ".clawd-tank" / "sessions.json"


def save_sessions(
    sessions: dict[str, dict],
    path: Path = SESSIONS_PATH,
    *,
    order: list[tuple[str, int]] | None = None,
    next_id: int | None = None,
) -> None:
    """Save session states to JSON atomically. Sets are converted to sorted lists.

    Optional order and next_id persist session arrival order with stable display IDs.

    `last_event_monotonic` is in-memory only and excluded from the persisted state.
    """
    serializable = {}
    for sid, state in sessions.items():
        entry = {k: v for k, v in state.items() if k != "last_event_monotonic"}
        if "subagents" in entry:
            entry["subagents"] = sorted(entry["subagents"])
        serializable[sid] = entry
    envelope: dict = {"sessions": serializable}
    if order is not None:
        envelope["session_order"] = [[sid, did] for sid, did in order]
    if next_id is not None:
        envelope["next_display_id"] = next_id
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(envelope, f)
        os.replace(tmp_path, str(path))
    except OSError:
        logger.warning("Failed to save session state to %s", path)
        try:
            os.unlink(tmp_path)
        except (OSError, UnboundLocalError):
            pass


def load_sessions(path: Path = SESSIONS_PATH) -> tuple[dict[str, dict], list[tuple[str, int]], int]:
    """Load session states from JSON. Returns (states, order, next_display_id).

    Returns empty defaults on any error. Entries missing required keys are dropped.
    Handles both old format (flat dict of sessions) and new envelope format.
    """
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return {}, [], 1

        # Detect format: new envelope has "sessions" key, old format has session IDs directly
        if "sessions" in data and isinstance(data["sessions"], dict):
            raw_sessions = data["sessions"]
            raw_order = data.get("session_order", [])
            next_id = data.get("next_display_id", 1)
        else:
            # Old format: flat dict of session_id → state
            raw_sessions = data
            raw_order = []
            next_id = 1

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

        # Parse session_order
        order: list[tuple[str, int]] = []
        if isinstance(raw_order, list):
            for entry in raw_order:
                if isinstance(entry, list) and len(entry) == 2:
                    sid, did = entry
                    if isinstance(sid, str) and isinstance(did, int):
                        order.append((sid, did))

        if not isinstance(next_id, int) or next_id < 1:
            next_id = 1

        # Synthesize order from session keys when missing (old format upgrade)
        if not order and valid:
            order = [(sid, i + 1) for i, sid in enumerate(valid)]
            next_id = len(valid) + 1

        return valid, order, next_id
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return {}, [], 1
