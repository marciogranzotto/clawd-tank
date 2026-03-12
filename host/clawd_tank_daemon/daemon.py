"""Clawd Tank daemon — bridges Claude Code hooks to ESP32 via BLE."""

import asyncio
import fcntl
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .ble_client import ClawdBleClient
from .protocol import daemon_message_to_ble_payload
from .socket_server import SocketServer

logger = logging.getLogger("clawd-tank")

PID_PATH = Path.home() / ".clawd-tank" / "daemon.pid"
LOCK_PATH = Path.home() / ".clawd-tank" / "daemon.lock"


@runtime_checkable
class DaemonObserver(Protocol):
    def on_connection_change(self, connected: bool) -> None: ...
    def on_notification_change(self, count: int) -> None: ...


def _acquire_lock() -> int:
    """Acquire an exclusive file lock. Returns the fd, or exits if another daemon is running."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        print("Another clawd-tank daemon is already running", file=sys.stderr)
        sys.exit(0)
    return fd


class ClawdDaemon:
    def __init__(self, observer: Optional["DaemonObserver"] = None, headless: bool = True):
        self._ble = ClawdBleClient(on_disconnect_cb=self._on_ble_disconnect)
        self._socket = SocketServer(on_message=self._handle_message)
        self._active_notifications: dict[str, dict] = {}
        self._pending_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._running = True
        self._shutdown_event = asyncio.Event()
        self._lock_fd: int | None = None
        self._observer = observer
        self._headless = headless

    async def _handle_message(self, msg: dict) -> None:
        """Handle a message from clawd-tank-notify via the socket."""
        event = msg.get("event")
        session_id = msg.get("session_id", "")

        if event == "add":
            self._active_notifications[session_id] = msg
        elif event == "dismiss":
            self._active_notifications.pop(session_id, None)

        await self._pending_queue.put(msg)

        if self._observer:
            self._observer.on_notification_change(len(self._active_notifications))

    async def _replay_active(self) -> None:
        """Replay all active notifications after reconnect."""
        logger.info("Replaying %d active notifications", len(self._active_notifications))
        for msg in list(self._active_notifications.values()):
            try:
                payload = daemon_message_to_ble_payload(msg)
            except ValueError:
                logger.error("Skipping unknown event in replay: %s", msg.get("event"))
                continue
            await self._ble.write_notification(payload)
            await asyncio.sleep(0.05)  # Small delay between writes

    def _write_pid(self) -> None:
        PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        PID_PATH.write_text(str(os.getpid()))

    def _remove_pid(self) -> None:
        if PID_PATH.exists():
            PID_PATH.unlink()

    async def _shutdown(self) -> None:
        logger.info("Shutting down...")
        self._running = False
        self._shutdown_event.set()

        # Send clear to ESP32
        clear_payload = daemon_message_to_ble_payload({"event": "clear"})
        await self._ble.write_notification(clear_payload)
        await self._ble.disconnect()
        await self._socket.stop()
        self._remove_pid()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _on_ble_disconnect(self) -> None:
        """Called by BLE client on disconnect."""
        if self._observer:
            self._observer.on_connection_change(False)

    async def _ble_sender(self) -> None:
        """Process pending messages and send them over BLE."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self._pending_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                payload = daemon_message_to_ble_payload(msg)
            except ValueError:
                logger.error("Skipping unknown event: %s", msg.get("event"))
                continue

            was_connected = self._ble.is_connected
            await self._ble.ensure_connected()
            if not was_connected and self._ble.is_connected and self._observer:
                self._observer.on_connection_change(True)

            success = await self._ble.write_notification(payload)

            if not success:
                was_connected = self._ble.is_connected
                await self._ble.ensure_connected()
                if not was_connected and self._ble.is_connected and self._observer:
                    self._observer.on_connection_change(True)
                await self._replay_active()

    async def run(self) -> None:
        """Main daemon loop."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )

        self._lock_fd = _acquire_lock()
        self._write_pid()

        if self._headless:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown()))

        await self._socket.start()

        # Start BLE connection in background (non-blocking)
        ble_connect_task = asyncio.create_task(self._ble.connect())
        sender_task = asyncio.create_task(self._ble_sender())

        # Wait until shutdown is signaled
        await self._shutdown_event.wait()

        # Cancel background tasks
        sender_task.cancel()
        ble_connect_task.cancel()
        for task in (sender_task, ble_connect_task):
            try:
                await task
            except asyncio.CancelledError:
                pass


def main():
    daemon = ClawdDaemon()
    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()
