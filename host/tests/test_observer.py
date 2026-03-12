# host/tests/test_observer.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from clawd_tank_daemon.daemon import ClawdDaemon, DaemonObserver


class MockObserver:
    def __init__(self):
        self.connection_changes = []
        self.notification_changes = []

    def on_connection_change(self, connected: bool) -> None:
        self.connection_changes.append(connected)

    def on_notification_change(self, count: int) -> None:
        self.notification_changes.append(count)


@pytest.mark.asyncio
async def test_observer_notification_add():
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    assert observer.notification_changes == [1]


@pytest.mark.asyncio
async def test_observer_notification_dismiss():
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    await daemon._handle_message({"event": "dismiss", "session_id": "s1"})
    assert observer.notification_changes == [1, 0]


@pytest.mark.asyncio
async def test_observer_notification_add_multiple():
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    await daemon._handle_message(
        {"event": "add", "session_id": "s2", "project": "p", "message": "m"}
    )
    assert observer.notification_changes == [1, 2]


@pytest.mark.asyncio
async def test_no_observer_does_not_crash():
    """ClawdDaemon without observer must work exactly as before."""
    daemon = ClawdDaemon()
    await daemon._handle_message(
        {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
    )
    assert "s1" in daemon._active_notifications


@pytest.mark.asyncio
async def test_observer_connection_via_disconnect_callback():
    """BLE client disconnect callback triggers observer."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    daemon._on_ble_disconnect()
    assert observer.connection_changes == [False]


@pytest.mark.asyncio
async def test_observer_connection_true_on_ble_sender_connect():
    """_ble_sender fires on_connection_change(True) after reconnect."""
    observer = MockObserver()
    daemon = ClawdDaemon(observer=observer)
    daemon._ble = AsyncMock()
    daemon._ble.is_connected = False

    async def fake_ensure():
        daemon._ble.is_connected = True

    daemon._ble.ensure_connected = AsyncMock(side_effect=fake_ensure)
    daemon._ble.write_notification = AsyncMock(return_value=True)

    await daemon._pending_queue.put(
        {"event": "dismiss", "session_id": "s1"}
    )

    sender = asyncio.create_task(daemon._ble_sender())
    await asyncio.sleep(0.1)
    daemon._running = False
    sender.cancel()
    try:
        await sender
    except asyncio.CancelledError:
        pass

    assert True in observer.connection_changes
