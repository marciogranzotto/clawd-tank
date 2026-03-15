"""Tests for daemon session state tracking and display state computation."""

import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from clawd_tank_daemon.daemon import ClawdDaemon


def make_daemon():
    """Create a daemon in sim-only mode with no actual transport."""
    d = ClawdDaemon(sim_only=True)
    d._transports.clear()
    d._transport_queues.clear()
    return d


def test_no_sessions_returns_sleeping():
    d = make_daemon()
    assert d._compute_display_state() == "sleeping"

def test_single_registered_session_returns_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "registered", "last_event": time.time()}
    assert d._compute_display_state() == "idle"

def test_single_idle_session_returns_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    assert d._compute_display_state() == "idle"

def test_single_thinking_session_returns_thinking():
    d = make_daemon()
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    assert d._compute_display_state() == "thinking"

def test_single_working_session_returns_working_1():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    assert d._compute_display_state() == "working_1"

def test_two_working_sessions_returns_working_2():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    d._session_states["s2"] = {"state": "working", "last_event": time.time()}
    assert d._compute_display_state() == "working_2"

def test_three_plus_working_sessions_returns_working_3():
    d = make_daemon()
    for i in range(5):
        d._session_states[f"s{i}"] = {"state": "working", "last_event": time.time()}
    assert d._compute_display_state() == "working_3"

def test_working_beats_thinking():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    d._session_states["s2"] = {"state": "thinking", "last_event": time.time()}
    assert d._compute_display_state() == "working_1"

def test_thinking_beats_confused():
    d = make_daemon()
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    d._session_states["s2"] = {"state": "confused", "last_event": time.time()}
    assert d._compute_display_state() == "thinking"

def test_confused_beats_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "confused", "last_event": time.time()}
    d._session_states["s2"] = {"state": "idle", "last_event": time.time()}
    assert d._compute_display_state() == "confused"

def test_registered_treated_as_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "registered", "last_event": time.time()}
    d._session_states["s2"] = {"state": "confused", "last_event": time.time()}
    assert d._compute_display_state() == "confused"


# --- Task 4: _handle_message wiring ---

@pytest.mark.asyncio
async def test_session_start_registers_session():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert "s1" in d._session_states
    assert d._session_states["s1"]["state"] == "registered"

@pytest.mark.asyncio
async def test_prompt_submit_sets_thinking():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "thinking"

@pytest.mark.asyncio
async def test_tool_use_sets_working():
    d = make_daemon()
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "working"

@pytest.mark.asyncio
async def test_stop_add_sets_idle():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"

@pytest.mark.asyncio
async def test_notification_add_sets_confused():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "Notification", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "confused"

@pytest.mark.asyncio
async def test_session_end_removes_session():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert "s1" not in d._session_states

@pytest.mark.asyncio
async def test_implicit_session_creation():
    d = make_daemon()
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert "s1" in d._session_states
    assert d._session_states["s1"]["state"] == "working"

@pytest.mark.asyncio
async def test_last_display_state_tracks_changes():
    d = make_daemon()
    assert d._last_display_state == "sleeping"
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert d._last_display_state == "idle"
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._last_display_state == "thinking"
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._last_display_state == "working_1"
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._last_display_state == "idle"
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert d._last_display_state == "sleeping"


# --- Task 5: staleness eviction and compact handling ---

def test_staleness_evicts_old_sessions():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time() - 9999}
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    assert "s1" not in d._session_states

def test_staleness_keeps_fresh_sessions():
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    d._session_staleness_timeout = 600
    d._evict_stale_sessions()
    assert "s1" in d._session_states

@pytest.mark.asyncio
async def test_compact_triggers_sweeping():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    transport = AsyncMock()
    transport.is_connected = True
    d._transports["test"] = transport
    d._transport_queues["test"] = asyncio.Queue()
    d._last_display_state = "working_1"

    await d._handle_message({"event": "compact", "session_id": "s1"})

    calls = transport.write_notification.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    assert any(p.get("status") == "sweeping" for p in payloads)
    assert any(p.get("status") == "working_1" for p in payloads)


# --- Subagent tracking ---

@pytest.mark.asyncio
async def test_subagent_start_tracks_agent_id():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert "a1" in d._session_states["s1"]["subagents"]

@pytest.mark.asyncio
async def test_subagent_stop_removes_agent_id():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time(), "subagents": {"a1"}}
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert "a1" not in d._session_states["s1"].get("subagents", set())

@pytest.mark.asyncio
async def test_subagent_start_creates_session_if_missing():
    d = make_daemon()
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert "s1" in d._session_states
    assert "a1" in d._session_states["s1"]["subagents"]

@pytest.mark.asyncio
async def test_subagent_start_refreshes_last_event():
    d = make_daemon()
    old_time = time.time() - 500
    d._session_states["s1"] = {"state": "working", "last_event": old_time}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["last_event"] > old_time

@pytest.mark.asyncio
async def test_subagent_stop_refreshes_last_event():
    d = make_daemon()
    old_time = time.time() - 500
    d._session_states["s1"] = {"state": "working", "last_event": old_time, "subagents": {"a1"}}
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["last_event"] > old_time

@pytest.mark.asyncio
async def test_subagent_stop_for_unknown_agent_is_noop():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    # Should not crash
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "unknown"})
    assert d._session_states["s1"]["state"] == "working"

@pytest.mark.asyncio
async def test_multiple_subagents_tracked():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a2"})
    assert d._session_states["s1"]["subagents"] == {"a1", "a2"}
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["subagents"] == {"a2"}


@pytest.mark.asyncio
async def test_subagent_start_with_empty_agent_id_ignored():
    """Empty agent_id must not pollute the subagents set."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "idle", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": ""})
    assert not d._session_states["s1"].get("subagents")


# --- Task 4 / Task 5: eviction suppression and subagent display state ---

def test_staleness_skips_sessions_with_active_subagents():
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,
        "subagents": {"a1"},
    }
    d._evict_stale_sessions()
    assert "s1" in d._session_states  # NOT evicted


def test_staleness_evicts_after_all_subagents_stop():
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,
        "subagents": set(),  # empty — all subagents stopped
    }
    d._evict_stale_sessions()
    assert "s1" not in d._session_states  # evicted


def test_idle_session_with_subagents_counts_as_working():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),
        "subagents": {"a1"},
    }
    assert d._compute_display_state() == "working_1"


def test_multiple_sessions_with_subagents_count_working():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time(), "subagents": {"a1"},
    }
    d._session_states["s2"] = {
        "state": "working", "last_event": time.time(),
    }
    assert d._compute_display_state() == "working_2"


def test_session_with_empty_subagents_not_counted_as_working():
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),
        "subagents": set(),
    }
    assert d._compute_display_state() == "idle"


# --- Task 6: edge case tests and integration test ---

@pytest.mark.asyncio
async def test_session_end_clears_subagents():
    """SessionEnd removes session entirely, even with active subagents."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "working", "last_event": time.time(), "subagents": {"a1", "a2"},
    }
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert "s1" not in d._session_states
    # Subsequent SubagentStop for orphaned agent is safe no-op
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert "s1" not in d._session_states

@pytest.mark.asyncio
async def test_duplicate_subagent_start_is_idempotent():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert d._session_states["s1"]["subagents"] == {"a1"}

def test_working_session_with_subagents_counts_once():
    """A session that is both state=working AND has subagents counts as 1, not 2."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "working", "last_event": time.time(), "subagents": {"a1"},
    }
    assert d._compute_display_state() == "working_1"

@pytest.mark.asyncio
async def test_subagent_lifecycle_prevents_sleeping():
    """Full lifecycle: subagent keeps session working, Stop clears subagents."""
    d = make_daemon()

    # Session starts and begins working
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert d._compute_display_state() == "idle"

    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._compute_display_state() == "working_1"

    # Subagent spawned — session stays working
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    assert d._compute_display_state() == "working_1"
    assert "a1" in d._session_states["s1"]["subagents"]

    # Subagent keeps session alive during staleness check
    d._session_staleness_timeout = 0
    d._evict_stale_sessions()
    assert "s1" in d._session_states

    # Subagent finishes via SubagentStop
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert not d._session_states["s1"].get("subagents")

    # Session back to working (tool_use still active)
    assert d._compute_display_state() == "working_1"

    # Stop fires — session goes idle, subagents cleared
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"
    assert "subagents" not in d._session_states["s1"]
    assert d._compute_display_state() == "idle"

    # Now staleness check CAN evict
    d._session_states["s1"]["last_event"] = time.time() - 9999
    d._evict_stale_sessions()
    assert "s1" not in d._session_states
    assert d._compute_display_state() == "sleeping"


@pytest.mark.asyncio
async def test_stop_clears_stale_subagents():
    """Stop hook clears subagents even if SubagentStop was missed."""
    d = make_daemon()
    d._session_states["s1"] = {
        "state": "working", "last_event": time.time(),
        "subagents": {"orphan1", "orphan2"},
    }
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"
    assert "subagents" not in d._session_states["s1"]
    assert d._compute_display_state() == "idle"


# --- Session state persistence ---


def make_daemon_with_path(sessions_path):
    """Create a test daemon that uses a custom sessions file path."""
    d = ClawdDaemon(sim_only=True, sessions_path=sessions_path)
    d._transports.clear()
    d._transport_queues.clear()
    return d


@pytest.mark.asyncio
async def test_daemon_persists_on_handle_message(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert path.exists()
    data = json.loads(path.read_text())
    assert "s1" in data
    assert data["s1"]["state"] == "registered"


@pytest.mark.asyncio
async def test_daemon_does_not_persist_on_last_event_only_update(tmp_path):
    """tool_use when already working only updates last_event — no disk write."""
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    d._persist_sessions()  # initial save
    mtime_before = path.stat().st_mtime_ns
    import time as _time; _time.sleep(0.01)
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    mtime_after = path.stat().st_mtime_ns
    assert mtime_before == mtime_after


@pytest.mark.asyncio
async def test_daemon_persists_on_state_transition(tmp_path):
    """thinking → working is a structural change — should persist."""
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "thinking", "last_event": time.time()}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    data = json.loads(path.read_text())
    assert data["s1"]["state"] == "working"


def test_daemon_persists_on_eviction(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "idle", "last_event": time.time() - 9999}
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    data = json.loads(path.read_text())
    assert "s1" not in data


def test_daemon_loads_on_init(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "s1": {"state": "working", "last_event": time.time()},
    }))
    d = ClawdDaemon(sim_only=True, sessions_path=path)
    d._transports.clear()
    d._transport_queues.clear()
    assert "s1" in d._session_states
    assert d._session_states["s1"]["state"] == "working"


def test_daemon_loads_subagents_as_sets(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "s1": {
            "state": "idle",
            "last_event": time.time(),
            "subagents": ["a1", "a2"],
        },
    }))
    d = ClawdDaemon(sim_only=True, sessions_path=path)
    d._transports.clear()
    d._transport_queues.clear()
    assert d._session_states["s1"]["subagents"] == {"a1", "a2"}
    assert isinstance(d._session_states["s1"]["subagents"], set)


def test_daemon_startup_display_state_from_loaded_sessions(tmp_path):
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "s1": {"state": "working", "last_event": time.time()},
    }))
    d = ClawdDaemon(sim_only=True, sessions_path=path)
    d._transports.clear()
    d._transport_queues.clear()
    assert d._compute_display_state() == "working_1"


def test_daemon_evicts_stale_sessions_on_startup(tmp_path):
    """Stale sessions from disk are evicted immediately, not after 30s."""
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps({
        "stale": {"state": "working", "last_event": time.time() - 9999},
        "fresh": {"state": "idle", "last_event": time.time()},
    }))
    d = ClawdDaemon(sim_only=True, sessions_path=path)
    d._transports.clear()
    d._transport_queues.clear()
    assert "stale" not in d._session_states
    assert "fresh" in d._session_states


@pytest.mark.asyncio
async def test_session_end_persists_removal(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    data = json.loads(path.read_text())
    assert "s1" not in data
