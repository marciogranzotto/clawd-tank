"""Clawd daemon — bridges Claude Code hooks to ESP32 via BLE."""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from .ble_client import ClawdBleClient
from .protocol import daemon_message_to_ble_payload
from .socket_server import SocketServer

logger = logging.getLogger("clawd")

PID_PATH = Path.home() / ".clawd" / "daemon.pid"


class ClawdDaemon:
    def __init__(self):
        self._ble = ClawdBleClient()
        self._socket = SocketServer(on_message=self._handle_message)
        self._active_notifications: dict[str, dict] = {}
        self._pending_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._running = True
        self._shutdown_event = asyncio.Event()

    async def _handle_message(self, msg: dict) -> None:
        """Handle a message from clawd-notify via the socket."""
        event = msg.get("event")
        session_id = msg.get("session_id", "")

        if event == "add":
            self._active_notifications[session_id] = msg
        elif event == "dismiss":
            self._active_notifications.pop(session_id, None)

        await self._pending_queue.put(msg)

    async def _replay_active(self) -> None:
        """Replay all active notifications after reconnect."""
        logger.info("Replaying %d active notifications", len(self._active_notifications))
        for msg in list(self._active_notifications.values()):
            payload = daemon_message_to_ble_payload(msg)
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

    async def _ble_sender(self) -> None:
        """Process pending messages and send them over BLE."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self._pending_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await self._ble.ensure_connected()

            payload = daemon_message_to_ble_payload(msg)
            success = await self._ble.write_notification(payload)

            if not success:
                await self._ble.ensure_connected()
                await self._replay_active()

    async def run(self) -> None:
        """Main daemon loop."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )

        self._write_pid()

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
