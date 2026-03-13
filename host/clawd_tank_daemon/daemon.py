"""Clawd Tank daemon — bridges Claude Code hooks to ESP32 via BLE."""

import asyncio
import fcntl
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .ble_client import ClawdBleClient
from .protocol import daemon_message_to_ble_payload
from .sim_client import SimClient, SIM_DEFAULT_PORT
from .socket_server import SocketServer
from .transport import TransportClient

logger = logging.getLogger("clawd-tank")

PID_PATH = Path.home() / ".clawd-tank" / "daemon.pid"
LOCK_PATH = Path.home() / ".clawd-tank" / "daemon.lock"


@runtime_checkable
class DaemonObserver(Protocol):
    def on_connection_change(self, connected: bool) -> None: ...
    def on_notification_change(self, count: int) -> None: ...


def _stop_existing_daemon() -> bool:
    """Send SIGTERM to an existing daemon and wait for it to exit. Returns True if stopped."""
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text().strip())
    except (ValueError, OSError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True  # already dead
    except PermissionError:
        return False
    # Wait up to 3 seconds for it to release the lock
    import time
    for _ in range(30):
        try:
            os.kill(pid, 0)  # check if still alive
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    logger.warning("Existing daemon (PID %d) did not exit in time", pid)
    return False


def _acquire_lock(takeover: bool = False) -> int:
    """Acquire an exclusive file lock.

    If takeover is True (menu bar mode), stop the existing daemon first.
    If takeover is False (headless mode), exit if another daemon is running.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if takeover:
            logger.info("Stopping existing daemon to take over...")
            _stop_existing_daemon()
            # Retry the lock
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                print("Could not acquire lock after stopping existing daemon", file=sys.stderr)
                sys.exit(1)
        else:
            os.close(fd)
            print("Another clawd-tank daemon is already running", file=sys.stderr)
            sys.exit(0)
    return fd


class ClawdDaemon:
    def __init__(
        self,
        observer: Optional["DaemonObserver"] = None,
        headless: bool = True,
        sim_port: int = 0,
        sim_only: bool = False,
    ):
        self._transports: dict[str, TransportClient] = {}
        self._transport_queues: dict[str, asyncio.Queue] = {}

        if not sim_only:
            ble = ClawdBleClient(
                on_disconnect_cb=lambda: self._on_transport_disconnect("ble"),
                on_connect_cb=lambda: self._on_transport_connect("ble"),
            )
            self._transports["ble"] = ble
            self._transport_queues["ble"] = asyncio.Queue()

        if sim_port > 0:
            sim = SimClient(
                port=sim_port,
                on_disconnect_cb=lambda: self._on_transport_disconnect("sim"),
                on_connect_cb=lambda: self._on_transport_connect("sim"),
            )
            self._transports["sim"] = sim
            self._transport_queues["sim"] = asyncio.Queue()

        self._sender_tasks: dict[str, asyncio.Task] = {}
        self._socket = SocketServer(on_message=self._handle_message)
        self._active_notifications: dict[str, dict] = {}
        self._running = True
        self._shutdown_event = asyncio.Event()
        self._lock_fd: int | None = None
        self._observer = observer
        self._headless = headless

    async def _handle_message(self, msg: dict) -> None:
        """Handle a message from clawd-tank-notify via the socket."""
        event = msg.get("event")
        session_id = msg.get("session_id", "")
        logger.info("Socket msg: event=%s session=%s project=%s",
                     event, session_id[:12], msg.get("project", "?"))

        if event == "add":
            self._active_notifications[session_id] = msg
        elif event == "dismiss":
            self._active_notifications.pop(session_id, None)

        for q in self._transport_queues.values():
            await q.put(msg)

        if self._observer:
            self._observer.on_notification_change(len(self._active_notifications))

    def _on_transport_connect(self, name: str) -> None:
        """Called by a transport client on successful connection."""
        logger.info("Transport '%s' connected", name)
        if self._observer:
            self._observer.on_connection_change(True)

    def _on_transport_disconnect(self, name: str) -> None:
        """Called by a transport client on disconnect."""
        logger.warning("Transport '%s' disconnected", name)
        if self._observer:
            any_connected = any(t.is_connected for t in self._transports.values())
            self._observer.on_connection_change(any_connected)

    async def _sync_time_for(self, transport) -> None:
        """Send current host time and timezone to a transport."""
        epoch = int(time.time())
        # Build POSIX TZ string from local UTC offset
        # POSIX TZ signs are inverted: UTC+3 means 3 hours *west* of Greenwich
        utc_offset = time.localtime().tm_gmtoff  # seconds east of UTC
        sign = "-" if utc_offset >= 0 else "+"  # inverted for POSIX
        abs_offset = abs(utc_offset)
        hours, remainder = divmod(abs_offset, 3600)
        minutes = remainder // 60
        tz = f"UTC{sign}{hours}" if minutes == 0 else f"UTC{sign}{hours}:{minutes:02d}"
        payload = json.dumps({"action": "set_time", "epoch": epoch, "tz": tz})
        await transport.write_notification(payload)
        logger.info("Synced time: epoch %d, tz %s", epoch, tz)

    async def _replay_active_for(self, transport) -> None:
        """Replay all active notifications to a transport after reconnect."""
        logger.info("Replaying %d active notifications", len(self._active_notifications))
        for msg in list(self._active_notifications.values()):
            try:
                payload = daemon_message_to_ble_payload(msg)
            except ValueError:
                continue
            await transport.write_notification(payload)
            await asyncio.sleep(0.05)

    async def _transport_sender(self, name: str) -> None:
        """Process pending messages and send them over a named transport."""
        transport = self._transports[name]
        queue = self._transport_queues[name]
        # Initial connection — retries until connected
        await transport.ensure_connected()
        if transport.is_connected:
            await self._sync_time_for(transport)
            await self._replay_active_for(transport)
        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                payload = daemon_message_to_ble_payload(msg)
            except ValueError:
                logger.error("[%s] Skipping unknown event: %s", name, msg.get("event"))
                continue

            was_connected = transport.is_connected
            await transport.ensure_connected()
            if not was_connected and transport.is_connected:
                await self._sync_time_for(transport)

            success = await transport.write_notification(payload)

            if not success:
                was_connected = transport.is_connected
                await transport.ensure_connected()
                if not was_connected and transport.is_connected:
                    await self._sync_time_for(transport)
                await self._replay_active_for(transport)

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

        for task in self._sender_tasks.values():
            task.cancel()
        for task in self._sender_tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._sender_tasks.clear()

        clear_payload = daemon_message_to_ble_payload({"event": "clear"})
        for transport in self._transports.values():
            if transport.is_connected:
                await transport.write_notification(clear_payload)
            await transport.disconnect()
        await self._socket.stop()
        self._remove_pid()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    async def read_config(self) -> dict:
        """Read config from the first connected transport."""
        for transport in self._transports.values():
            if transport.is_connected:
                return await transport.read_config()
        return {}

    async def write_config(self, payload: str) -> bool:
        """Write config to all connected transports."""
        success = False
        for transport in self._transports.values():
            if transport.is_connected:
                if await transport.write_config(payload):
                    success = True
        return success

    async def reconnect(self) -> None:
        """Force reconnect on all transports."""
        for transport in self._transports.values():
            await transport.ensure_connected()

    async def run(self) -> None:
        """Main daemon loop."""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )

        self._lock_fd = _acquire_lock(takeover=not self._headless)
        self._write_pid()

        if self._headless:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self._shutdown()))

        await self._socket.start()

        for name in self._transports:
            # Each sender handles its own connect via ensure_connected()
            self._sender_tasks[name] = asyncio.create_task(self._transport_sender(name))

        await self._shutdown_event.wait()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Clawd Tank daemon")
    parser.add_argument("--sim", action="store_true",
                        help="Enable simulator transport (BLE + TCP)")
    parser.add_argument("--sim-only", action="store_true",
                        help="Simulator only (no BLE)")
    parser.add_argument("--sim-port", type=int, default=SIM_DEFAULT_PORT,
                        help=f"Simulator TCP port (default: {SIM_DEFAULT_PORT})")
    args = parser.parse_args()

    sim_port = 0
    if args.sim or args.sim_only or args.sim_port != SIM_DEFAULT_PORT:
        sim_port = args.sim_port

    daemon = ClawdDaemon(sim_port=sim_port, sim_only=args.sim_only)
    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()
