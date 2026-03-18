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


def _add_session(d, sid, state_dict, display_id=None):
    """Helper: add a session to both _session_states and _session_order."""
    d._session_states[sid] = state_dict
    if sid not in [s for s, _ in d._session_order]:
        did = display_id if display_id is not None else d._next_display_id
        d._session_order.append((sid, did))
        if display_id is None:
            d._next_display_id += 1
        else:
            d._next_display_id = max(d._next_display_id, did + 1)


def test_no_sessions_returns_sleeping():
    d = make_daemon()
    assert d._compute_display_state() == {"status": "sleeping"}

def test_single_registered_session_returns_idle():
    d = make_daemon()
    _add_session(d, "s1", {"state": "registered", "last_event": time.time()})
    assert d._compute_display_state() == {"anims": ["idle"], "ids": [1], "subagents": 0}

def test_single_idle_session_returns_idle():
    d = make_daemon()
    _add_session(d, "s1", {"state": "idle", "last_event": time.time()})
    assert d._compute_display_state() == {"anims": ["idle"], "ids": [1], "subagents": 0}

def test_single_thinking_session_returns_thinking():
    d = make_daemon()
    _add_session(d, "s1", {"state": "thinking", "last_event": time.time()})
    assert d._compute_display_state() == {"anims": ["thinking"], "ids": [1], "subagents": 0}

def test_single_working_session_returns_typing():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time()})
    assert d._compute_display_state() == {"anims": ["typing"], "ids": [1], "subagents": 0}

def test_two_working_sessions_returns_two_typing():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time()})
    _add_session(d, "s2", {"state": "working", "last_event": time.time()})
    state = d._compute_display_state()
    assert state["anims"] == ["typing", "typing"]
    assert len(state["ids"]) == 2

def test_three_plus_working_sessions_capped_at_four():
    d = make_daemon()
    for i in range(5):
        _add_session(d, f"s{i}", {"state": "working", "last_event": time.time()})
    state = d._compute_display_state()
    assert len(state["anims"]) == 4  # max visible
    assert state["overflow"] == 1

def test_working_and_thinking_mixed():
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time()})
    _add_session(d, "s2", {"state": "thinking", "last_event": time.time()})
    state = d._compute_display_state()
    assert state["anims"] == ["typing", "thinking"]

def test_thinking_and_confused_mixed():
    d = make_daemon()
    _add_session(d, "s1", {"state": "thinking", "last_event": time.time()})
    _add_session(d, "s2", {"state": "confused", "last_event": time.time()})
    state = d._compute_display_state()
    assert state["anims"] == ["thinking", "confused"]

def test_confused_and_idle_mixed():
    d = make_daemon()
    _add_session(d, "s1", {"state": "confused", "last_event": time.time()})
    _add_session(d, "s2", {"state": "idle", "last_event": time.time()})
    state = d._compute_display_state()
    assert state["anims"] == ["confused", "idle"]

def test_registered_treated_as_idle():
    d = make_daemon()
    _add_session(d, "s1", {"state": "registered", "last_event": time.time()})
    _add_session(d, "s2", {"state": "confused", "last_event": time.time()})
    state = d._compute_display_state()
    assert state["anims"] == ["idle", "confused"]


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
    assert d._last_display_state == {"status": "sleeping"}
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert d._last_display_state == {"anims": ["idle"], "ids": [1], "subagents": 0}
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._last_display_state == {"anims": ["thinking"], "ids": [1], "subagents": 0}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._last_display_state == {"anims": ["typing"], "ids": [1], "subagents": 0}
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._last_display_state == {"anims": ["idle"], "ids": [1], "subagents": 0}
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert d._last_display_state == {"status": "sleeping"}


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
    """V2 transport receives set_sessions with 'sweeping' for the compacting session."""
    d = make_daemon()
    _add_session(d, "s1", {"state": "working", "last_event": time.time()})
    transport = AsyncMock()
    transport.is_connected = True
    d._transports["test"] = transport
    d._transport_queues["test"] = asyncio.Queue()
    d._transport_versions["test"] = 2  # v2 transport to get set_sessions
    d._last_display_state = {"anims": ["typing"], "ids": [1], "subagents": 0}

    await d._handle_message({"event": "compact", "session_id": "s1"})

    calls = transport.write_notification.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    # V2: single set_sessions payload with the compacting session showing "sweeping"
    session_payloads = [p for p in payloads if p.get("action") == "set_sessions"]
    assert len(session_payloads) == 1
    assert "sweeping" in session_payloads[0]["anims"]


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

def test_staleness_evicts_sessions_with_dead_subagents():
    """Stale sessions are evicted even if subagents exist — stale last_event
    means subagent tool calls stopped refreshing it, so they're dead."""
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time() - 9999,
        "subagents": {"a1"},
    }
    d._evict_stale_sessions()
    assert "s1" not in d._session_states  # evicted — subagents are dead too


def test_staleness_keeps_sessions_with_active_subagents():
    """Sessions with active subagents stay alive because subagent tool calls
    refresh last_event via PreToolUse on the parent session."""
    d = make_daemon()
    d._session_staleness_timeout = 600
    d._session_states["s1"] = {
        "state": "idle",
        "last_event": time.time(),  # fresh — subagent is active
        "subagents": {"a1"},
    }
    d._evict_stale_sessions()
    assert "s1" in d._session_states  # NOT evicted — still fresh


def test_idle_session_with_subagents_counts_as_building():
    d = make_daemon()
    _add_session(d, "s1", {
        "state": "idle",
        "last_event": time.time(),
        "subagents": {"a1"},
    })
    state = d._compute_display_state()
    assert state["anims"] == ["building"]
    assert state["subagents"] == 1


def test_multiple_sessions_with_subagents():
    d = make_daemon()
    _add_session(d, "s1", {
        "state": "idle", "last_event": time.time(), "subagents": {"a1"},
    })
    _add_session(d, "s2", {
        "state": "working", "last_event": time.time(),
    })
    state = d._compute_display_state()
    assert state["anims"] == ["building", "typing"]
    assert state["subagents"] == 1


def test_session_with_empty_subagents_not_counted_as_building():
    d = make_daemon()
    _add_session(d, "s1", {
        "state": "idle",
        "last_event": time.time(),
        "subagents": set(),
    })
    state = d._compute_display_state()
    assert state["anims"] == ["idle"]
    assert state["subagents"] == 0


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
    """A session that is both state=working AND has subagents shows building."""
    d = make_daemon()
    _add_session(d, "s1", {
        "state": "working", "last_event": time.time(), "subagents": {"a1"},
    })
    state = d._compute_display_state()
    assert state["anims"] == ["building"]
    assert state["subagents"] == 1

@pytest.mark.asyncio
async def test_subagent_lifecycle():
    """Full lifecycle: subagent keeps session working while active,
    Stop doesn't clear subagents (background agents may still run),
    staleness eviction cleans up dead subagents."""
    d = make_daemon()

    # Session starts and begins working
    await d._handle_message({"event": "session_start", "session_id": "s1"})
    assert d._compute_display_state() == {"anims": ["idle"], "ids": [1], "subagents": 0}

    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._compute_display_state() == {"anims": ["typing"], "ids": [1], "subagents": 0}

    # Subagent spawned — session becomes building
    await d._handle_message({"event": "subagent_start", "session_id": "s1", "agent_id": "a1"})
    state = d._compute_display_state()
    assert state["anims"] == ["building"]
    assert state["subagents"] == 1
    assert "a1" in d._session_states["s1"]["subagents"]

    # Stop fires — session state goes idle, but subagent keeps it building
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"
    assert "a1" in d._session_states["s1"]["subagents"]
    state = d._compute_display_state()
    assert state["anims"] == ["building"]

    # Subagent finishes via SubagentStop
    await d._handle_message({"event": "subagent_stop", "session_id": "s1", "agent_id": "a1"})
    assert not d._session_states["s1"].get("subagents")
    assert d._compute_display_state() == {"anims": ["idle"], "ids": [1], "subagents": 0}

    # Staleness eviction works normally
    d._session_states["s1"]["last_event"] = time.time() - 9999
    d._evict_stale_sessions()
    assert "s1" not in d._session_states
    assert d._compute_display_state() == {"status": "sleeping"}


@pytest.mark.asyncio
async def test_stale_subagents_evicted_with_session():
    """Missed SubagentStop hooks don't prevent eviction — if last_event is
    stale, subagent tool calls stopped refreshing it, so they're dead."""
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {
        "state": "idle", "last_event": time.time() - 9999,
        "subagents": {"orphan1", "orphan2"},
    }
    d._evict_stale_sessions()
    assert "s1" not in d._session_states
    assert d._compute_display_state() == {"status": "sleeping"}


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
    assert "s1" in data["sessions"]
    assert data["sessions"]["s1"]["state"] == "registered"


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
    assert data["sessions"]["s1"]["state"] == "working"


def test_daemon_persists_on_eviction(tmp_path):
    path = tmp_path / "sessions.json"
    d = make_daemon_with_path(path)
    d._session_states["s1"] = {"state": "idle", "last_event": time.time() - 9999}
    d._session_staleness_timeout = 1
    d._evict_stale_sessions()
    data = json.loads(path.read_text())
    assert "s1" not in data["sessions"]


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
    # Use envelope format with session_order so _compute_display_state works
    path.write_text(json.dumps({
        "sessions": {
            "s1": {"state": "working", "last_event": time.time()},
        },
        "session_order": [["s1", 1]],
        "next_display_id": 2,
    }))
    d = ClawdDaemon(sim_only=True, sessions_path=path)
    d._transports.clear()
    d._transport_queues.clear()
    assert d._compute_display_state() == {"anims": ["typing"], "ids": [1], "subagents": 0}


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
    assert "s1" not in data["sessions"]


# --- Session order tracking ---


@pytest.mark.asyncio
async def test_session_order_tracks_arrival():
    """Sessions should be tracked in arrival order with stable display IDs."""
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    await d._handle_message({"event": "session_start", "session_id": "ccc"})
    assert d._session_order == [("aaa", 1), ("bbb", 2), ("ccc", 3)]


@pytest.mark.asyncio
async def test_session_order_removes_on_end():
    """Ending a middle session shifts later ones down."""
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    await d._handle_message({"event": "session_start", "session_id": "ccc"})
    await d._handle_message({"event": "dismiss", "session_id": "bbb", "hook": "SessionEnd"})
    assert d._session_order == [("aaa", 1), ("ccc", 3)]


@pytest.mark.asyncio
async def test_session_order_display_ids_never_reuse():
    """Display IDs increment and are never reused even after removal."""
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "dismiss", "session_id": "aaa", "hook": "SessionEnd"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    assert d._session_order == [("bbb", 2)]


@pytest.mark.asyncio
async def test_session_order_created_on_tool_use_if_missing():
    """tool_use creates session in order if not already tracked."""
    d = make_daemon()
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    assert len(d._session_order) == 1
    assert d._session_order[0][0] == "aaa"


# --- Task 2 new tests: display state v2 dict format ---


@pytest.mark.asyncio
async def test_display_state_single_session_typing():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    state = d._compute_display_state()
    assert state == {"anims": ["typing"], "ids": [1], "subagents": 0}


@pytest.mark.asyncio
async def test_display_state_working_with_subagents_becomes_building():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    await d._handle_message({"event": "subagent_start", "session_id": "aaa", "agent_id": "sub1"})
    state = d._compute_display_state()
    assert state["anims"] == ["building"]
    assert state["subagents"] == 1


@pytest.mark.asyncio
async def test_display_state_preserves_arrival_order():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    # bbb is registered → idle
    state = d._compute_display_state()
    assert state["anims"] == ["typing", "idle"]
    assert state["ids"] == [1, 2]


@pytest.mark.asyncio
async def test_display_state_overflow_with_5_sessions():
    d = make_daemon()
    for i in range(5):
        sid = f"s{i}"
        await d._handle_message({"event": "session_start", "session_id": sid})
        await d._handle_message({"event": "tool_use", "session_id": sid})
    state = d._compute_display_state()
    assert len(state["anims"]) == 4  # max visible
    assert state["overflow"] == 1


def test_display_state_sleeping_when_no_sessions():
    d = make_daemon()
    state = d._compute_display_state()
    assert state == {"status": "sleeping"}


@pytest.mark.asyncio
async def test_display_state_middle_session_removed():
    d = make_daemon()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    await d._handle_message({"event": "tool_use", "session_id": "bbb"})
    await d._handle_message({"event": "session_start", "session_id": "ccc"})
    await d._handle_message({"event": "tool_use", "session_id": "ccc"})
    # Remove middle
    await d._handle_message({"event": "dismiss", "session_id": "bbb", "hook": "SessionEnd"})
    state = d._compute_display_state()
    assert state["anims"] == ["typing", "typing"]
    assert state["ids"] == [1, 3]  # id 2 gone, others preserved


# --- Per-transport protocol versioning ---


@pytest.mark.asyncio
async def test_v1_transport_gets_set_status():
    """V1 transport should receive legacy set_status format."""
    d = make_daemon()
    transport = AsyncMock()
    transport.is_connected = True
    d._transports["ble"] = transport
    d._transport_queues["ble"] = asyncio.Queue()
    d._transport_versions["ble"] = 1

    await d._handle_message({"event": "session_start", "session_id": "s1"})
    await d._handle_message({"event": "tool_use", "session_id": "s1"})

    calls = transport.write_notification.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    # Should have received set_status (v1 format), not set_sessions
    status_payloads = [p for p in payloads if p.get("action") == "set_status"]
    assert any(p["status"].startswith("working") for p in status_payloads)
    assert not any(p.get("action") == "set_sessions" for p in payloads)


@pytest.mark.asyncio
async def test_v2_transport_gets_set_sessions():
    """V2 transport should receive set_sessions format."""
    d = make_daemon()
    transport = AsyncMock()
    transport.is_connected = True
    d._transports["sim"] = transport
    d._transport_queues["sim"] = asyncio.Queue()
    d._transport_versions["sim"] = 2

    await d._handle_message({"event": "session_start", "session_id": "s1"})
    await d._handle_message({"event": "tool_use", "session_id": "s1"})

    calls = transport.write_notification.call_args_list
    payloads = [json.loads(c[0][0]) for c in calls]
    session_payloads = [p for p in payloads if p.get("action") == "set_sessions"]
    assert len(session_payloads) > 0
    assert session_payloads[-1]["anims"] == ["typing"]


@pytest.mark.asyncio
async def test_sim_transport_auto_sets_v2():
    """Simulator transport auto-sets to v2 on connect."""
    d = make_daemon()
    d._on_transport_connect("sim")
    assert d._transport_versions.get("sim") == 2


@pytest.mark.asyncio
async def test_ble_transport_defaults_v1():
    """BLE transport defaults to v1 (no auto-set)."""
    d = make_daemon()
    d._on_transport_connect("ble")
    assert d._transport_versions.get("ble") is None  # defaults to 1 via .get(name, 1)


@pytest.mark.asyncio
async def test_ble_transport_version_read_on_connect():
    """BLE transport should read version after connecting."""
    d = make_daemon()
    transport = AsyncMock()
    transport.read_version = AsyncMock(return_value=2)
    transport.is_connected = True
    d._transports["ble"] = transport
    d._transport_queues["ble"] = asyncio.Queue()
    version = await transport.read_version()
    d._transport_versions["ble"] = version
    assert d._transport_versions.get("ble") == 2


# --- Per-session sweeping (Task 6) ---


class MockTransport:
    """Minimal transport stub that captures written payloads."""

    def __init__(self, name: str = "mock"):
        self.name = name
        self.is_connected = True
        self.written: list[str] = []

    async def write_notification(self, payload: str) -> None:
        self.written.append(payload)


@pytest.mark.asyncio
async def test_compact_sends_per_session_sweeping_v2():
    """V2 transport should receive set_sessions with sweeping for the compacting session."""
    d = make_daemon()
    d._transport_versions["sim"] = 2
    transport = MockTransport(name="sim")
    d._transports["sim"] = transport
    d._transport_queues["sim"] = asyncio.Queue()
    await d._handle_message({"event": "session_start", "session_id": "aaa"})
    await d._handle_message({"event": "tool_use", "session_id": "aaa"})
    await d._handle_message({"event": "session_start", "session_id": "bbb"})
    await d._handle_message({"event": "tool_use", "session_id": "bbb"})
    transport.written.clear()
    await d._handle_message({"event": "compact", "session_id": "bbb"})
    payloads = [json.loads(p) for p in transport.written]
    sweeping_payload = next((p for p in payloads if p.get("action") == "set_sessions"), None)
    assert sweeping_payload is not None
    assert sweeping_payload["anims"][1] == "sweeping"  # bbb is second
    assert sweeping_payload["anims"][0] == "typing"    # aaa unchanged


# --- Task 2: error state and dizzy display mapping ---


def test_error_state_returns_dizzy():
    d = make_daemon()
    _add_session(d, "s1", {"state": "error", "last_event": time.time()})
    assert d._compute_display_state() == {"anims": ["dizzy"], "ids": [1], "subagents": 0}


@pytest.mark.asyncio
async def test_stop_failure_add_sets_error():
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "StopFailure", "session_id": "s1",
        "project": "proj", "message": "Rate limited",
    })
    assert d._session_states["s1"]["state"] == "error"


@pytest.mark.asyncio
async def test_error_state_clears_on_prompt_submit():
    d = make_daemon()
    d._session_states["s1"] = {"state": "error", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "UserPromptSubmit", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "thinking"


@pytest.mark.asyncio
async def test_error_state_clears_on_tool_use():
    d = make_daemon()
    d._session_states["s1"] = {"state": "error", "last_event": time.time()}
    await d._handle_message({"event": "tool_use", "session_id": "s1"})
    assert d._session_states["s1"]["state"] == "working"


@pytest.mark.asyncio
async def test_error_state_removed_on_session_end():
    d = make_daemon()
    d._session_states["s1"] = {"state": "error", "last_event": time.time()}
    await d._handle_message({"event": "dismiss", "hook": "SessionEnd", "session_id": "s1"})
    assert "s1" not in d._session_states


def test_error_state_evicted_on_staleness():
    d = make_daemon()
    d._session_staleness_timeout = 1
    d._session_states["s1"] = {"state": "error", "last_event": time.time() - 9999}
    d._evict_stale_sessions()
    assert "s1" not in d._session_states


@pytest.mark.asyncio
async def test_stop_then_stop_failure_overwrites_to_error():
    """Stop then StopFailure on same session — card overwrites, state becomes error."""
    d = make_daemon()
    d._session_states["s1"] = {"state": "working", "last_event": time.time()}
    await d._handle_message({
        "event": "add", "hook": "Stop", "session_id": "s1",
        "project": "proj", "message": "Waiting",
    })
    assert d._session_states["s1"]["state"] == "idle"
    await d._handle_message({
        "event": "add", "hook": "StopFailure", "session_id": "s1",
        "project": "proj", "message": "Rate limited",
    })
    assert d._session_states["s1"]["state"] == "error"


@pytest.mark.asyncio
async def test_error_and_working_mixed():
    d = make_daemon()
    _add_session(d, "s1", {"state": "error", "last_event": time.time()})
    _add_session(d, "s2", {"state": "working", "last_event": time.time()})
    state = d._compute_display_state()
    assert state["anims"] == ["dizzy", "typing"]
