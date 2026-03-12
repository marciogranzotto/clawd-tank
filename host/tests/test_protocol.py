import json
from clawd_daemon.protocol import (
    hook_payload_to_daemon_message,
    daemon_message_to_ble_payload,
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
