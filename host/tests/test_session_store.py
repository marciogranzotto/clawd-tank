"""Tests for session state persistence."""

import json
import time
import pytest
from pathlib import Path

from clawd_tank_daemon.session_store import save_sessions, load_sessions


def test_round_trip_basic(tmp_path):
    path = tmp_path / "sessions.json"
    states = {
        "s1": {"state": "working", "last_event": 1234567890.0},
        "s2": {"state": "idle", "last_event": 1234567891.0},
    }
    save_sessions(states, path)
    loaded, _, _ = load_sessions(path)
    assert loaded == states


def test_round_trip_with_subagents(tmp_path):
    path = tmp_path / "sessions.json"
    states = {
        "s1": {
            "state": "idle",
            "last_event": 1234567890.0,
            "subagents": {"a1", "a2"},
        },
    }
    save_sessions(states, path)
    loaded, _, _ = load_sessions(path)
    assert loaded["s1"]["subagents"] == {"a1", "a2"}
    assert isinstance(loaded["s1"]["subagents"], set)


def test_round_trip_empty_subagents(tmp_path):
    path = tmp_path / "sessions.json"
    states = {
        "s1": {"state": "idle", "last_event": 1234567890.0, "subagents": set()},
    }
    save_sessions(states, path)
    loaded, _, _ = load_sessions(path)
    assert loaded["s1"]["subagents"] == set()


def test_round_trip_empty_dict(tmp_path):
    path = tmp_path / "sessions.json"
    save_sessions({}, path)
    loaded, _, _ = load_sessions(path)
    assert loaded == {}


def test_load_missing_file(tmp_path):
    path = tmp_path / "nonexistent.json"
    loaded, order, next_id = load_sessions(path)
    assert loaded == {}
    assert order == []
    assert next_id == 1


def test_load_corrupt_file(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("not valid json {{{")
    loaded, _, _ = load_sessions(path)
    assert loaded == {}


def test_load_empty_file(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text("")
    loaded, _, _ = load_sessions(path)
    assert loaded == {}


def test_load_invalid_entries_skipped(tmp_path):
    """Entries missing required keys are dropped."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "sessions": {
            "good": {"state": "idle", "last_event": 1.0},
            "bad_no_state": {"last_event": 1.0},
            "bad_no_event": {"state": "idle"},
            "bad_garbage": {"foo": "bar"},
        },
    }))
    loaded, _, _ = load_sessions(path)
    assert "good" in loaded
    assert "bad_no_state" not in loaded
    assert "bad_no_event" not in loaded
    assert "bad_garbage" not in loaded


def test_save_creates_parent_dirs(tmp_path):
    path = tmp_path / "nested" / "dir" / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": 1.0}}, path)
    assert path.exists()
    loaded, _, _ = load_sessions(path)
    assert "s1" in loaded


def test_save_overwrites_existing(tmp_path):
    path = tmp_path / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": 1.0}}, path)
    save_sessions({"s2": {"state": "working", "last_event": 2.0}}, path)
    loaded, _, _ = load_sessions(path)
    assert "s1" not in loaded
    assert "s2" in loaded


def test_load_non_dict_values_skipped(tmp_path):
    """Entry values that are not dicts are dropped."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "sessions": {
            "good": {"state": "idle", "last_event": 1.0},
            "number": 42,
            "string": "garbage",
            "list": [1, 2, 3],
        },
    }))
    loaded, _, _ = load_sessions(path)
    assert "good" in loaded
    assert "number" not in loaded
    assert "string" not in loaded
    assert "list" not in loaded


def test_save_is_atomic(tmp_path):
    """No partial files left behind — temp files cleaned up."""
    path = tmp_path / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": 1.0}}, path)
    # Only the target file should exist, no .tmp files
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name == "sessions.json"


def test_load_old_format_backwards_compat(tmp_path):
    """Old format (flat dict, no envelope) synthesizes order from session keys."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "s1": {"state": "working", "last_event": 1234567890.0},
    }))
    loaded, order, next_id = load_sessions(path)
    assert "s1" in loaded
    assert loaded["s1"]["state"] == "working"
    assert order == [("s1", 1)]
    assert next_id == 2


# --- Session order persistence ---


def test_session_order_round_trip(tmp_path):
    path = tmp_path / "sessions.json"
    states = {"aaa": {"state": "working", "last_event": 100.0}}
    order = [("aaa", 1), ("bbb", 2)]
    save_sessions(states, path, order=order, next_id=3)
    loaded_states, loaded_order, loaded_next_id = load_sessions(path)
    assert loaded_order == [("aaa", 1), ("bbb", 2)]
    assert loaded_next_id == 3


def test_session_order_default_empty(tmp_path):
    """When saved without order, loading synthesizes order from valid sessions."""
    path = tmp_path / "sessions.json"
    save_sessions({"s1": {"state": "idle", "last_event": 1.0}}, path)
    _, order, next_id = load_sessions(path)
    assert order == [("s1", 1)]
    assert next_id == 2


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
