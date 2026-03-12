import asyncio
import pytest
from clawd_daemon.daemon import ClawdDaemon


@pytest.mark.asyncio
async def test_handle_add_tracks_notification():
    daemon = ClawdDaemon()
    msg = {"event": "add", "session_id": "s1", "project": "proj", "message": "hi"}
    await daemon._handle_message(msg)
    assert "s1" in daemon._active_notifications
    assert daemon._pending_queue.qsize() == 1


@pytest.mark.asyncio
async def test_handle_dismiss_removes_notification():
    daemon = ClawdDaemon()
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    await daemon._handle_message({"event": "dismiss", "session_id": "s1"})
    assert "s1" not in daemon._active_notifications
    assert daemon._pending_queue.qsize() == 2


@pytest.mark.asyncio
async def test_dismiss_unknown_is_safe():
    daemon = ClawdDaemon()
    await daemon._handle_message({"event": "dismiss", "session_id": "nope"})
    assert daemon._pending_queue.qsize() == 1


# --- Edge cases ---

@pytest.mark.asyncio
async def test_duplicate_add_updates_not_duplicates():
    """Adding the same session_id twice must update the entry, not create two."""
    daemon = ClawdDaemon()
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "first"}
    )
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "updated"}
    )
    assert len(daemon._active_notifications) == 1
    assert daemon._active_notifications["s1"]["message"] == "updated"
    # Both adds go to the queue for BLE delivery
    assert daemon._pending_queue.qsize() == 2


@pytest.mark.asyncio
async def test_empty_session_id_add_and_dismiss():
    """Empty-string session_id must be tracked and dismissable."""
    daemon = ClawdDaemon()
    await daemon._handle_message(
        {"event": "add", "session_id": "", "project": "p", "message": "m"}
    )
    assert "" in daemon._active_notifications

    await daemon._handle_message({"event": "dismiss", "session_id": ""})
    assert "" not in daemon._active_notifications


@pytest.mark.asyncio
async def test_multiple_sessions_independent():
    """Multiple independent session IDs must not interfere with each other."""
    daemon = ClawdDaemon()
    for sid in ("s1", "s2", "s3"):
        await daemon._handle_message(
            {"event": "add", "session_id": sid, "project": "p", "message": "m"}
        )
    assert len(daemon._active_notifications) == 3

    await daemon._handle_message({"event": "dismiss", "session_id": "s2"})
    assert len(daemon._active_notifications) == 2
    assert "s1" in daemon._active_notifications
    assert "s2" not in daemon._active_notifications
    assert "s3" in daemon._active_notifications
