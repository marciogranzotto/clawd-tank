import json
from clawd_tank_daemon.protocol import (
    hook_payload_to_daemon_message,
    daemon_message_to_ble_payload,
    display_state_to_ble_payload,
    display_state_to_v1_payload,
)


def test_idle_prompt_to_add():
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "abc-123",
        "cwd": "/Users/me/Projects/my-project",
        "message": "Claude is waiting for input",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "add"
    assert msg["session_id"] == "abc-123"
    assert msg["project"] == "my-project"
    assert msg["message"] == "Claude is waiting for input"


def test_prompt_submit_to_dismiss():
    hook = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "abc-123",
        "cwd": "/Users/me/Projects/my-project",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["session_id"] == "abc-123"


def test_session_end_to_dismiss():
    hook = {
        "hook_event_name": "SessionEnd",
        "session_id": "abc-123",
        "cwd": "/Users/me/Projects/foo",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["session_id"] == "abc-123"


def test_irrelevant_notification_ignored():
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "auth_success",
        "session_id": "abc-123",
        "cwd": "/tmp",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is None


def test_daemon_add_to_ble():
    msg = {"event": "add", "session_id": "s1", "project": "proj", "message": "hi"}
    ble = daemon_message_to_ble_payload(msg)
    parsed = json.loads(ble)
    assert parsed["action"] == "add"
    assert parsed["id"] == "s1"
    assert parsed["project"] == "proj"
    assert parsed["message"] == "hi"


def test_daemon_dismiss_to_ble():
    msg = {"event": "dismiss", "session_id": "s1"}
    ble = daemon_message_to_ble_payload(msg)
    parsed = json.loads(ble)
    assert parsed["action"] == "dismiss"
    assert parsed["id"] == "s1"


# --- Edge cases ---

def test_unknown_hook_event_returns_none():
    """Unrecognised event names must be silently dropped."""
    assert hook_payload_to_daemon_message({"hook_event_name": "SomeFutureEvent"}) is None
    assert hook_payload_to_daemon_message({}) is None  # completely empty payload


def test_missing_session_id_defaults_to_empty_string():
    """Missing session_id should default to "" not raise."""
    hook = {
        "hook_event_name": "UserPromptSubmit",
        # no session_id
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["session_id"] == ""


def test_empty_session_id_passthrough():
    """Empty-string session_id is valid and must round-trip correctly."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "",
        "cwd": "/Users/me/Projects/my-project",
        "message": "waiting",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["session_id"] == ""


def test_missing_cwd_gives_unknown_project():
    """When cwd is absent the project should fall back to 'unknown'."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        # no cwd
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["project"] == "unknown"


def test_cwd_trailing_slash_gives_project_name():
    """cwd ending with '/' must still yield the directory name, not ''."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        "cwd": "/Users/me/Projects/my-project/",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    # Should NOT be empty — ideally "my-project"
    assert msg["project"] != "", (
        "Trailing slash in cwd causes basename to return '' — project name lost"
    )


def test_cwd_empty_string_gives_unknown_project():
    """cwd='' (explicit empty string) must fall back to 'unknown'."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        "cwd": "",
        "message": "waiting",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["project"] == "unknown", (
        f"Expected 'unknown' for empty cwd, got '{msg['project']}'"
    )


def test_missing_message_field_uses_default():
    """When message is absent the default 'Waiting for input' must be used."""
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        "cwd": "/tmp/proj",
        # no message
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["message"] == "Waiting for input"


def test_ble_payload_clear_event():
    """Clear event must produce {"action": "clear"} with no extra fields."""
    ble = daemon_message_to_ble_payload({"event": "clear"})
    parsed = json.loads(ble)
    assert parsed == {"action": "clear"}


def test_ble_payload_unknown_event_raises():
    """Unknown event must raise ValueError, not silently produce bad output."""
    import pytest
    with pytest.raises(ValueError):
        daemon_message_to_ble_payload({"event": "bogus"})


# --- New hook event types ---

def test_session_start_produces_session_start_event():
    hook = {
        "hook_event_name": "SessionStart",
        "session_id": "sess-1",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "session_start"
    assert msg["session_id"] == "sess-1"


def test_pre_tool_use_produces_tool_use_event():
    hook = {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-2",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "tool_use"
    assert msg["session_id"] == "sess-2"


def test_pre_compact_produces_compact_event():
    hook = {
        "hook_event_name": "PreCompact",
        "session_id": "sess-3",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "compact"
    assert msg["session_id"] == "sess-3"


# --- Hook discriminator field ---

def test_stop_add_includes_hook_discriminator():
    hook = {
        "hook_event_name": "Stop",
        "session_id": "s1",
        "cwd": "/tmp/my-project",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "add"
    assert msg["hook"] == "Stop"


def test_notification_add_includes_hook_discriminator():
    hook = {
        "hook_event_name": "Notification",
        "notification_type": "idle_prompt",
        "session_id": "s1",
        "cwd": "/tmp/my-project",
        "message": "idle",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "add"
    assert msg["hook"] == "Notification"


def test_prompt_submit_dismiss_includes_hook_discriminator():
    hook = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "s1",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["hook"] == "UserPromptSubmit"


def test_session_end_dismiss_includes_hook_discriminator():
    hook = {
        "hook_event_name": "SessionEnd",
        "session_id": "s1",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "dismiss"
    assert msg["hook"] == "SessionEnd"


def test_session_events_produce_no_ble_payload():
    """session_start, tool_use, compact return None from daemon_message_to_ble_payload."""
    assert daemon_message_to_ble_payload({"event": "session_start", "session_id": "s"}) is None
    assert daemon_message_to_ble_payload({"event": "tool_use", "session_id": "s"}) is None
    assert daemon_message_to_ble_payload({"event": "compact", "session_id": "s"}) is None


def test_subagent_start_produces_subagent_start_event():
    hook = {
        "hook_event_name": "SubagentStart",
        "session_id": "sess-1",
        "agent_id": "agent-abc123",
        "agent_type": "Explore",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "subagent_start"
    assert msg["session_id"] == "sess-1"
    assert msg["agent_id"] == "agent-abc123"


def test_subagent_stop_produces_subagent_stop_event():
    hook = {
        "hook_event_name": "SubagentStop",
        "session_id": "sess-1",
        "agent_id": "agent-abc123",
        "agent_type": "Explore",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "subagent_stop"
    assert msg["session_id"] == "sess-1"
    assert msg["agent_id"] == "agent-abc123"


def test_subagent_events_produce_no_ble_payload():
    assert daemon_message_to_ble_payload({"event": "subagent_start", "session_id": "s", "agent_id": "a"}) is None
    assert daemon_message_to_ble_payload({"event": "subagent_stop", "session_id": "s", "agent_id": "a"}) is None


# --- Display state payload generation (v2 protocol) ---


def test_display_state_to_payload_v2_sessions():
    state = {"anims": ["typing", "thinking"], "ids": [1, 2], "subagents": 3}
    payload = display_state_to_ble_payload(state)
    parsed = json.loads(payload)
    assert parsed["action"] == "set_sessions"
    assert parsed["anims"] == ["typing", "thinking"]
    assert parsed["ids"] == [1, 2]
    assert parsed["subagents"] == 3
    assert "overflow" not in parsed


def test_display_state_to_payload_v2_with_overflow():
    state = {"anims": ["typing"] * 4, "ids": [1, 2, 3, 4], "subagents": 0, "overflow": 2}
    payload = display_state_to_ble_payload(state)
    parsed = json.loads(payload)
    assert parsed["overflow"] == 2


def test_display_state_to_payload_sleeping():
    state = {"status": "sleeping"}
    payload = display_state_to_ble_payload(state)
    parsed = json.loads(payload)
    assert parsed == {"action": "set_status", "status": "sleeping"}


def test_display_state_to_payload_v1_fallback():
    """For v1 transports, convert dict state to legacy set_status string."""
    state = {"anims": ["typing", "thinking"], "ids": [1, 2], "subagents": 3}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["action"] == "set_status"
    assert parsed["status"] == "working_1"  # 1 typing session counts as working


def test_display_state_to_v1_two_working():
    state = {"anims": ["typing", "typing"], "ids": [1, 2], "subagents": 0}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "working_2"


def test_display_state_to_v1_building_counts_as_working():
    state = {"anims": ["building", "typing"], "ids": [1, 2], "subagents": 1}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "working_2"


def test_display_state_to_v1_capped_at_3():
    state = {"anims": ["typing", "typing", "building", "typing"], "ids": [1, 2, 3, 4], "subagents": 1}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "working_3"


def test_display_state_to_v1_thinking():
    state = {"anims": ["thinking"], "ids": [1], "subagents": 0}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "thinking"


def test_display_state_to_v1_confused():
    state = {"anims": ["confused"], "ids": [1], "subagents": 0}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "confused"


def test_display_state_to_v1_idle():
    state = {"anims": ["idle"], "ids": [1], "subagents": 0}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "idle"


def test_display_state_to_v1_sleeping():
    state = {"status": "sleeping"}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed == {"action": "set_status", "status": "sleeping"}


# --- StopFailure hook ---

def test_stop_failure_produces_add_event():
    hook = {
        "hook_event_name": "StopFailure",
        "session_id": "abc-123",
        "cwd": "/Users/me/Projects/my-project",
        "error": "Rate limit reached",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["event"] == "add"
    assert msg["hook"] == "StopFailure"
    assert msg["session_id"] == "abc-123"
    assert msg["project"] == "my-project"
    assert msg["message"] == "Rate limit reached"


def test_stop_failure_fallback_message():
    hook = {
        "hook_event_name": "StopFailure",
        "session_id": "abc-123",
        "cwd": "/tmp/proj",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg is not None
    assert msg["message"] == "API error"


def test_stop_failure_ble_payload_includes_alert():
    msg = {
        "event": "add",
        "hook": "StopFailure",
        "session_id": "s1",
        "project": "proj",
        "message": "Rate limited",
    }
    ble = daemon_message_to_ble_payload(msg)
    parsed = json.loads(ble)
    assert parsed["action"] == "add"
    assert parsed["alert"] == "error"


def test_normal_add_ble_payload_no_alert():
    msg = {
        "event": "add",
        "hook": "Stop",
        "session_id": "s1",
        "project": "proj",
        "message": "Waiting",
    }
    ble = daemon_message_to_ble_payload(msg)
    parsed = json.loads(ble)
    assert "alert" not in parsed


def test_stop_failure_stop_reason_fallback():
    hook = {
        "hook_event_name": "StopFailure",
        "session_id": "s1",
        "cwd": "/tmp/proj",
        "stop_reason": "max_turns",
    }
    msg = hook_payload_to_daemon_message(hook)
    assert msg["message"] == "max_turns"


def test_display_state_to_v1_dizzy_maps_to_confused():
    state = {"anims": ["dizzy"], "ids": [1], "subagents": 0}
    payload = display_state_to_v1_payload(state)
    parsed = json.loads(payload)
    assert parsed["status"] == "confused"
