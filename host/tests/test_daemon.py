import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch
from clawd_tank_daemon.daemon import ClawdDaemon


class FakeObserver:
    def __init__(self):
        self.connection_changes = []
        self.notification_changes = []

    def on_connection_change(self, connected: bool, transport: str = "") -> None:
        self.connection_changes.append((connected, transport))

    def on_notification_change(self, count: int) -> None:
        self.notification_changes.append(count)


@pytest.mark.asyncio
async def test_handle_add_tracks_notification():
    daemon = ClawdDaemon()
    msg = {"event": "add", "session_id": "s1", "project": "proj", "message": "hi"}
    await daemon._handle_message(msg)
    assert "s1" in daemon._active_notifications
    assert daemon._transport_queues["ble"].qsize() == 1


@pytest.mark.asyncio
async def test_handle_dismiss_removes_notification():
    daemon = ClawdDaemon()
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    await daemon._handle_message({"event": "dismiss", "session_id": "s1"})
    assert "s1" not in daemon._active_notifications
    assert daemon._transport_queues["ble"].qsize() == 2


@pytest.mark.asyncio
async def test_dismiss_unknown_is_safe():
    daemon = ClawdDaemon()
    await daemon._handle_message({"event": "dismiss", "session_id": "nope"})
    assert daemon._transport_queues["ble"].qsize() == 1


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
    assert daemon._transport_queues["ble"].qsize() == 2


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


@pytest.mark.asyncio
async def test_unknown_event_does_not_crash_sender():
    """An unknown event in the queue must be logged and skipped, not crash _transport_sender."""
    daemon = ClawdDaemon()
    await daemon._handle_message({"event": "bogus", "session_id": "x"})
    await daemon._handle_message({"event": "dismiss", "session_id": "x"})

    from clawd_tank_daemon.protocol import daemon_message_to_ble_payload
    with pytest.raises(ValueError):
        daemon_message_to_ble_payload({"event": "bogus"})

    assert daemon._transport_queues["ble"].qsize() == 2


@pytest.mark.asyncio
async def test_ble_sender_skips_unknown_event():
    """_transport_sender must skip unknown events and continue processing the queue."""
    daemon = ClawdDaemon()
    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    mock_transport.ensure_connected = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)
    daemon._transports["ble"] = mock_transport

    await daemon._transport_queues["ble"].put({"event": "bogus", "session_id": "x"})
    await daemon._transport_queues["ble"].put({"event": "dismiss", "session_id": "d1"})

    sender = asyncio.create_task(daemon._transport_sender("ble"))
    await asyncio.sleep(0.1)
    daemon._running = False
    sender.cancel()
    try:
        await sender
    except asyncio.CancelledError:
        pass

    assert mock_transport.write_notification.call_count >= 1


# --- _replay_active_for ---

@pytest.mark.asyncio
async def test_replay_active_sends_all_active_notifications():
    """_replay_active_for must write every currently active notification."""
    daemon = ClawdDaemon()
    mock_transport = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)

    # Populate active notifications directly (bypassing the queue)
    daemon._active_notifications = {
        "s1": {"event": "add", "session_id": "s1", "project": "p1", "message": "m1"},
        "s2": {"event": "add", "session_id": "s2", "project": "p2", "message": "m2"},
        "s3": {"event": "add", "session_id": "s3", "project": "p3", "message": "m3"},
    }

    await daemon._replay_active_for(mock_transport)

    # 3 notifications + 1 set_status call
    assert mock_transport.write_notification.call_count == 4
    # Verify payloads contain the right session IDs (exclude set_status payload)
    written_args = [call.args[0] for call in mock_transport.write_notification.call_args_list]
    import json
    written_ids = {json.loads(p)["id"] for p in written_args if "id" in json.loads(p)}
    assert written_ids == {"s1", "s2", "s3"}


@pytest.mark.asyncio
async def test_replay_active_empty_store_sends_nothing():
    """_replay_active_for with no active notifications only sends set_status."""
    daemon = ClawdDaemon()
    mock_transport = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)

    await daemon._replay_active_for(mock_transport)

    # Only the set_status payload is sent even with no notifications
    assert mock_transport.write_notification.call_count == 1
    payload = json.loads(mock_transport.write_notification.call_args[0][0])
    assert payload["action"] == "set_status"


@pytest.mark.asyncio
async def test_replay_active_skips_unknown_events():
    """_replay_active_for must skip entries with unknown events rather than crashing."""
    daemon = ClawdDaemon()
    mock_transport = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)

    daemon._active_notifications = {
        "s1": {"event": "add", "session_id": "s1", "project": "p", "message": "m"},
        "bad": {"event": "bogus", "session_id": "bad"},
    }

    # Should not raise — bad entry is skipped, valid one is sent, plus set_status
    await daemon._replay_active_for(mock_transport)
    assert mock_transport.write_notification.call_count == 2


@pytest.mark.asyncio
async def test_replay_active_concurrent_mutation_is_safe():
    """_replay_active_for snapshots active notifications so concurrent mutation doesn't crash."""
    daemon = ClawdDaemon()

    write_calls = []

    async def slow_write(payload):
        write_calls.append(payload)
        # Simulate a slow write; concurrent task mutates _active_notifications
        await asyncio.sleep(0.01)
        return True

    mock_transport = AsyncMock()
    mock_transport.write_notification = slow_write

    daemon._active_notifications = {
        "s1": {"event": "add", "session_id": "s1", "project": "p", "message": "m"},
        "s2": {"event": "add", "session_id": "s2", "project": "p", "message": "m"},
    }

    async def mutate():
        # Remove s2 and add s3 while replay is in progress
        await asyncio.sleep(0.005)
        daemon._active_notifications.pop("s2", None)
        daemon._active_notifications["s3"] = {
            "event": "add", "session_id": "s3", "project": "p", "message": "m"
        }

    # Run replay and mutation concurrently
    await asyncio.gather(daemon._replay_active_for(mock_transport), mutate())

    # Replay used a snapshot so it sent s1 and s2 (the state at snapshot time)
    # Plus the set_status payload at the end
    import json
    replayed_ids = {json.loads(p)["id"] for p in write_calls if "id" in json.loads(p)}
    assert replayed_ids == {"s1", "s2"}


# --- Transport write failure -> reconnect -> replay ---

@pytest.mark.asyncio
async def test_ble_write_failure_triggers_reconnect_and_replay():
    """When write_notification returns False, _transport_sender reconnects and replays."""
    daemon = ClawdDaemon()
    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    daemon._transports["ble"] = mock_transport

    # Initial _post_connect_sync writes: set_time, replay s1, set_status (3 writes).
    # Write 4 is the queued s2 — we make it fail to exercise the reconnect branch.
    write_calls = []

    async def mock_write(payload):
        write_calls.append(payload)
        return len(write_calls) != 4  # fail the 4th write

    mock_transport.write_notification = mock_write
    mock_transport.ensure_connected = AsyncMock()

    # Pre-populate one active notification for replay
    daemon._active_notifications = {
        "s1": {"event": "add", "session_id": "s1", "project": "p", "message": "m"},
    }

    # Enqueue the message that will fail on first write
    await daemon._transport_queues["ble"].put(
        {"event": "add", "session_id": "s2", "project": "p", "message": "m"}
    )

    sender = asyncio.create_task(daemon._transport_sender("ble"))
    await asyncio.sleep(0.2)
    daemon._running = False
    sender.cancel()
    try:
        await sender
    except asyncio.CancelledError:
        pass

    # ensure_connected must have been called at least twice (initial + reconnect)
    assert mock_transport.ensure_connected.call_count >= 2
    # write_notification called: once for the failing write, once for replay of s1
    assert len(write_calls) >= 2
    # After the failed write, post-connect sync must have re-sent set_time —
    # otherwise the board keeps stale time across reconnects (e.g. after Mac sleep).
    set_time_count = sum(
        1 for p in write_calls if json.loads(p).get("action") == "set_time"
    )
    assert set_time_count >= 2, f"expected >=2 set_time payloads, got {set_time_count}"


@pytest.mark.asyncio
async def test_ble_write_failure_replays_multiple_active():
    """After a write failure, all active notifications are replayed in order."""
    daemon = ClawdDaemon()
    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    daemon._transports["ble"] = mock_transport

    write_calls = []
    call_count = [0]

    async def mock_write(payload):
        call_count[0] += 1
        write_calls.append(payload)
        # Fail on the 5th write (the queued dismiss).
        # Writes 1-4 are: initial sync_time, initial replay s1, initial replay s2, set_status.
        if call_count[0] == 5:
            return False
        return True

    mock_transport.write_notification = mock_write
    mock_transport.ensure_connected = AsyncMock()

    daemon._active_notifications = {
        "s1": {"event": "add", "session_id": "s1", "project": "p", "message": "m1"},
        "s2": {"event": "add", "session_id": "s2", "project": "p", "message": "m2"},
    }

    await daemon._transport_queues["ble"].put(
        {"event": "dismiss", "session_id": "s_gone"}
    )

    sender = asyncio.create_task(daemon._transport_sender("ble"))
    await asyncio.sleep(0.3)
    daemon._running = False
    sender.cancel()
    try:
        await sender
    except asyncio.CancelledError:
        pass

    # write_calls: [0]=sync_time, [1]=replay s1, [2]=replay s2, [3]=set_status,
    #              [4]=failing dismiss, [5+]=replay writes after failure
    replayed_ids = {json.loads(p).get("id") for p in write_calls[5:] if json.loads(p).get("id")}
    assert "s1" in replayed_ids
    assert "s2" in replayed_ids


# --- Manual reconnect ---

@pytest.mark.asyncio
async def test_reconnect_forces_disconnect_and_resyncs_time():
    """Daemon.reconnect() (menu bar button) must force a fresh link and re-send set_time.

    Regression: on macOS sleep/wake the bleak is_connected property stays stale-True,
    so a plain ensure_connected() does nothing. The Reconnect button has to actively
    drop the client and then run _post_connect_sync so the board's clock catches up.
    """
    daemon = ClawdDaemon()
    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    write_calls = []

    async def mock_write(payload):
        write_calls.append(payload)
        return True

    mock_transport.write_notification = mock_write
    mock_transport.disconnect = AsyncMock()
    mock_transport.ensure_connected = AsyncMock()
    daemon._transports["ble"] = mock_transport

    await daemon.reconnect()

    assert mock_transport.disconnect.await_count == 1
    assert mock_transport.ensure_connected.await_count == 1
    actions = [json.loads(p).get("action") for p in write_calls]
    assert "set_time" in actions


@pytest.mark.asyncio
async def test_reconnect_continues_when_one_transport_fails():
    """A failing disconnect/connect on one transport must not block the others."""
    daemon = ClawdDaemon(sim_port=19872)
    ble = AsyncMock()
    ble.is_connected = True
    ble.disconnect = AsyncMock(side_effect=RuntimeError("boom"))
    ble.ensure_connected = AsyncMock(side_effect=RuntimeError("still broken"))
    ble.write_notification = AsyncMock(return_value=True)

    sim_calls = []

    async def sim_write(payload):
        sim_calls.append(payload)
        return True

    sim = AsyncMock()
    sim.is_connected = True
    sim.disconnect = AsyncMock()
    sim.ensure_connected = AsyncMock()
    sim.write_notification = sim_write

    daemon._transports["ble"] = ble
    daemon._transports["sim"] = sim

    await daemon.reconnect()

    # sim transport still got its full resync despite ble blowing up
    assert sim.disconnect.await_count == 1
    assert sim.ensure_connected.await_count == 1
    assert any(json.loads(p).get("action") == "set_time" for p in sim_calls)


# --- Multi-transport ---

@pytest.mark.asyncio
async def test_handle_message_broadcasts_to_all_transport_queues():
    """When sim is enabled, messages go to all transport queues."""
    daemon = ClawdDaemon(sim_port=19872)
    msg = {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    await daemon._handle_message(msg)
    for q in daemon._transport_queues.values():
        assert q.qsize() == 1


@pytest.mark.asyncio
async def test_sim_only_mode_has_no_ble_transport():
    """In sim-only mode, only the sim transport exists."""
    daemon = ClawdDaemon(sim_port=19872, sim_only=True)
    assert "ble" not in daemon._transports
    assert "sim" in daemon._transports


@pytest.mark.asyncio
async def test_transport_sender_replays_active_on_initial_connect():
    """Sender replays active notifications after initial connect + sync_time."""
    daemon = ClawdDaemon()
    daemon._active_notifications = {
        "s1": {"event": "add", "session_id": "s1", "project": "p", "message": "m1"},
    }

    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    mock_transport.ensure_connected = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)
    daemon._transports["ble"] = mock_transport

    sender = asyncio.create_task(daemon._transport_sender("ble"))
    await asyncio.sleep(0.3)
    daemon._running = False
    sender.cancel()
    try:
        await sender
    except asyncio.CancelledError:
        pass

    # Calls: sync_time, replay (1 notification), set_status = exactly 3
    write_calls = mock_transport.write_notification.call_args_list
    assert len(write_calls) == 3
    # Second call should be the replayed notification
    replayed = json.loads(write_calls[1][0][0])
    assert replayed["action"] == "add"
    assert replayed["id"]  # has an id field
    # Third call should be the set_status
    status = json.loads(write_calls[2][0][0])
    assert status["action"] == "set_status"


# --- add_transport / remove_transport ---

@pytest.mark.asyncio
async def test_add_transport_creates_queue_and_sender():
    """add_transport registers transport, creates queue, starts sender."""
    daemon = ClawdDaemon()

    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    mock_transport.ensure_connected = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)

    await daemon.add_transport("sim", mock_transport)

    assert "sim" in daemon._transports
    assert "sim" in daemon._transport_queues
    assert "sim" in daemon._sender_tasks
    assert not daemon._sender_tasks["sim"].done()

    # Clean up
    daemon._running = False
    daemon._sender_tasks["sim"].cancel()
    try:
        await daemon._sender_tasks["sim"]
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_remove_transport_cancels_sender_and_disconnects():
    """remove_transport cancels sender task and disconnects client."""
    daemon = ClawdDaemon()

    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    mock_transport.ensure_connected = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)
    mock_transport.disconnect = AsyncMock()

    await daemon.add_transport("sim", mock_transport)
    assert "sim" in daemon._sender_tasks

    await daemon.remove_transport("sim")

    assert "sim" not in daemon._transports
    assert "sim" not in daemon._transport_queues
    assert "sim" not in daemon._sender_tasks
    mock_transport.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_transport_wires_callbacks():
    """add_transport sets connect/disconnect callbacks on the client."""
    daemon = ClawdDaemon()
    obs = FakeObserver()
    daemon._observer = obs

    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    mock_transport.ensure_connected = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)
    mock_transport._on_connect_cb = None
    mock_transport._on_disconnect_cb = None

    await daemon.add_transport("sim", mock_transport)

    # Callbacks should be wired
    assert mock_transport._on_connect_cb is not None
    assert mock_transport._on_disconnect_cb is not None

    # Calling connect callback should notify observer
    mock_transport._on_connect_cb()
    assert len(obs.connection_changes) == 1
    assert obs.connection_changes[0] == (True, "sim")

    # Clean up
    daemon._running = False
    daemon._sender_tasks["sim"].cancel()
    try:
        await daemon._sender_tasks["sim"]
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_remove_transport_while_connecting():
    """remove_transport cancels sender even when blocked in connect retry loop."""
    daemon = ClawdDaemon()

    mock_transport = AsyncMock()
    mock_transport.is_connected = False
    # ensure_connected blocks indefinitely (simulates connect retry loop)
    mock_transport.ensure_connected = AsyncMock(side_effect=lambda: asyncio.sleep(999))
    mock_transport.write_notification = AsyncMock(return_value=True)
    mock_transport.disconnect = AsyncMock()

    await daemon.add_transport("sim", mock_transport)
    await asyncio.sleep(0.1)  # Let sender start and block in ensure_connected

    await daemon.remove_transport("sim")

    assert "sim" not in daemon._sender_tasks
    assert "sim" not in daemon._transports


@pytest.mark.asyncio
async def test_shutdown_cancels_dynamically_added_transport():
    """_shutdown cleans up sender tasks added via add_transport."""
    daemon = ClawdDaemon()

    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    mock_transport.ensure_connected = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)
    mock_transport.disconnect = AsyncMock()

    await daemon.add_transport("sim", mock_transport)
    assert "sim" in daemon._sender_tasks

    await daemon._shutdown()

    assert daemon._sender_tasks == {}
    mock_transport.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_handle_message_broadcasts_to_dynamically_added_transport():
    """Messages broadcast to transports added via add_transport."""
    daemon = ClawdDaemon()

    mock_transport = AsyncMock()
    mock_transport.is_connected = True
    mock_transport.ensure_connected = AsyncMock()
    mock_transport.write_notification = AsyncMock(return_value=True)

    await daemon.add_transport("sim", mock_transport)

    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )

    assert daemon._transport_queues["sim"].qsize() == 1

    # Clean up
    daemon._running = False
    daemon._sender_tasks["sim"].cancel()
    try:
        await daemon._sender_tasks["sim"]
    except asyncio.CancelledError:
        pass
