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
from .protocol import daemon_message_to_ble_payload, display_state_to_ble_payload, display_state_to_v1_payload
from .sim_client import SimClient, SIM_DEFAULT_PORT
from .socket_server import SocketServer
from .transport import TransportClient
from . import session_store
from .session_store import save_sessions, load_sessions

logger = logging.getLogger("clawd-tank")

PID_PATH = Path.home() / ".clawd-tank" / "daemon.pid"
LOCK_PATH = Path.home() / ".clawd-tank" / "daemon.lock"


@runtime_checkable
class DaemonObserver(Protocol):
    def on_connection_change(self, connected: bool, transport: str = "") -> None: ...
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
        sessions_path: Optional[Path] = None,
    ):
        self._transports: dict[str, TransportClient] = {}
        self._transport_queues: dict[str, asyncio.Queue] = {}

        if headless:
            # Headless (CLI) mode — create transports from args
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
        # Menu bar mode (headless=False): transports added later via add_transport()

        self._sender_tasks: dict[str, asyncio.Task] = {}
        self._socket = SocketServer(on_message=self._handle_message)
        self._active_notifications: dict[str, dict] = {}
        self._running = True
        self._shutdown_event = asyncio.Event()
        self._lock_fd: int | None = None
        self._observer = observer
        self._headless = headless
        self._sessions_path = sessions_path if sessions_path is not None else session_store.SESSIONS_PATH
        loaded_states, loaded_order, loaded_next_id = load_sessions(self._sessions_path)
        self._session_states: dict[str, dict] = loaded_states
        self._session_order: list[tuple[str, int]] = loaded_order
        self._next_display_id: int = loaded_next_id
        self._last_display_state: dict = {"status": "sleeping"}
        self._transport_versions: dict[str, int] = {}  # transport_name → protocol version
        self._session_staleness_timeout: float = 600.0
        self._evict_stale_sessions()

    async def _handle_message(self, msg: dict) -> None:
        """Handle a message from clawd-tank-notify via the socket."""
        event = msg.get("event")
        session_id = msg.get("session_id", "")
        hook = msg.get("hook", "")
        logger.info("Socket msg: event=%s hook=%s session=%s project=%s",
                     event, hook, session_id[:12], msg.get("project", "?"))

        if event == "add":
            self._active_notifications[session_id] = msg
        elif event == "dismiss":
            self._active_notifications.pop(session_id, None)

        changed = self._update_session_state(event, hook, session_id, msg.get("agent_id", ""))

        # --- Handle compact: send sweeping oneshot ---
        if event == "compact":
            computed = self._compute_display_state()
            for name, transport in self._transports.items():
                if not transport.is_connected:
                    continue
                version = self._transport_versions.get(name, 1)
                if version >= 2 and session_id:
                    # V2: send set_sessions with the compacting session's slot as "sweeping"
                    sweeping_state = self._compute_display_state()
                    if "anims" in sweeping_state:
                        for sid, did in self._session_order:
                            if sid == session_id:
                                idx = next(
                                    (i for i, d in enumerate(sweeping_state["ids"]) if d == did),
                                    None,
                                )
                                if idx is not None:
                                    sweeping_state["anims"][idx] = "sweeping"
                                break
                    payload = display_state_to_ble_payload(sweeping_state)
                    await transport.write_notification(payload)
                    # No second fallback for v2 — firmware handles sweeping as oneshot
                else:
                    # V1: send sweeping oneshot then restore display state
                    sweeping_payload = json.dumps({"action": "set_status", "status": "sweeping"})
                    await transport.write_notification(sweeping_payload)
                    fallback_payload = display_state_to_v1_payload(computed)
                    await transport.write_notification(fallback_payload)
            self._last_display_state = computed

        for q in self._transport_queues.values():
            await q.put(msg)

        if self._observer:
            self._observer.on_notification_change(len(self._active_notifications))

        if event != "compact":
            await self._broadcast_display_state_if_changed()

        if changed:
            self._persist_sessions()

    def _compute_display_state(self) -> dict:
        """Derive the display state from all active session states."""
        if not self._session_states:
            return {"status": "sleeping"}

        anims = []
        ids = []

        # Count subagents across ALL sessions, not just visible ones
        total_subagents = sum(
            len(s.get("subagents", set())) for s in self._session_states.values()
        )

        for session_id, display_id in self._session_order[:4]:
            state = self._session_states.get(session_id)
            if state is None:
                continue
            session_subagents = state.get("subagents", set())

            if state["state"] == "working" or session_subagents:
                if session_subagents:
                    anims.append("building")
                else:
                    anims.append("typing")
            elif state["state"] == "thinking":
                anims.append("thinking")
            elif state["state"] == "confused":
                anims.append("confused")
            else:
                anims.append("idle")
            ids.append(display_id)

        if not anims:
            return {"status": "sleeping"}

        result = {"anims": anims, "ids": ids, "subagents": total_subagents}
        if len(self._session_order) > 4:
            result["overflow"] = len(self._session_order) - 4
        return result

    def _update_session_state(self, event: str, hook: str, session_id: str, agent_id: str = "") -> bool:
        """Update per-session state based on a received event.

        Returns True if session state or subagents changed structurally
        (not just last_event), indicating the change should be persisted.
        """
        if not session_id:
            return False
        now = time.time()
        prev = self._session_states.get(session_id)
        prev_state = prev["state"] if prev else None
        prev_subagents = prev.get("subagents", set()).copy() if prev else None

        if event == "session_start":
            self._session_states[session_id] = {"state": "registered", "last_event": now}
        elif event == "tool_use":
            self._session_states.setdefault(session_id, {"state": "working", "last_event": now})
            self._session_states[session_id]["state"] = "working"
            self._session_states[session_id]["last_event"] = now
        elif event == "compact":
            if session_id in self._session_states:
                self._session_states[session_id]["last_event"] = now
        elif event == "add":
            self._session_states.setdefault(session_id, {"state": "idle", "last_event": now})
            if hook == "Stop":
                self._session_states[session_id]["state"] = "idle"
            elif hook == "Notification":
                self._session_states[session_id]["state"] = "confused"
            self._session_states[session_id]["last_event"] = now
        elif event == "dismiss":
            if hook == "SessionEnd":
                self._session_states.pop(session_id, None)
                self._session_order = [(sid, did) for sid, did in self._session_order if sid != session_id]
            elif hook == "UserPromptSubmit":
                self._session_states.setdefault(session_id, {"state": "thinking", "last_event": now})
                self._session_states[session_id]["state"] = "thinking"
                self._session_states[session_id]["last_event"] = now
            else:
                if session_id in self._session_states:
                    self._session_states[session_id]["last_event"] = now
        elif event == "subagent_start":
            if not agent_id:
                return False
            self._session_states.setdefault(session_id, {"state": "working", "last_event": now})
            self._session_states[session_id].setdefault("subagents", set())
            self._session_states[session_id]["subagents"].add(agent_id)
            self._session_states[session_id]["last_event"] = now
        elif event == "subagent_stop":
            if session_id in self._session_states:
                subagents = self._session_states[session_id].get("subagents")
                if subagents is not None:
                    subagents.discard(agent_id)
                self._session_states[session_id]["last_event"] = now

        # Track session order — append on first appearance
        cur = self._session_states.get(session_id)
        if cur is not None and session_id not in [sid for sid, _ in self._session_order]:
            self._session_order.append((session_id, self._next_display_id))
            self._next_display_id += 1

        if cur is None:
            return prev is not None  # session was removed
        return cur["state"] != prev_state or cur.get("subagents", set()) != (prev_subagents or set())

    def _evict_stale_sessions(self) -> None:
        # Active subagents refresh last_event via PreToolUse on the parent session.
        # If last_event is stale, subagents are dead too — safe to evict.
        now = time.time()
        stale = [
            sid for sid, s in self._session_states.items()
            if now - s["last_event"] > self._session_staleness_timeout
        ]
        for sid in stale:
            logger.info("Evicting stale session: %s", sid[:12])
            del self._session_states[sid]
        if stale:
            self._session_order = [(sid, did) for sid, did in self._session_order if sid not in stale]
            self._persist_sessions()

    def _persist_sessions(self) -> None:
        save_sessions(
            self._session_states,
            self._sessions_path,
            order=self._session_order,
            next_id=self._next_display_id,
        )

    async def _broadcast_display_state_if_changed(self) -> None:
        """Broadcast display state to all connected transports if changed."""
        new_state = self._compute_display_state()
        if new_state == self._last_display_state:
            return
        self._last_display_state = new_state
        for name, transport in self._transports.items():
            if transport.is_connected:
                version = self._transport_versions.get(name, 1)
                if version >= 2:
                    payload = display_state_to_ble_payload(new_state)
                else:
                    payload = display_state_to_v1_payload(new_state)
                await transport.write_notification(payload)

    async def _staleness_checker(self) -> None:
        while self._running:
            await asyncio.sleep(30)
            self._evict_stale_sessions()
            await self._broadcast_display_state_if_changed()

    def set_session_timeout(self, seconds: int) -> None:
        self._session_staleness_timeout = float(seconds)
        logger.info("Session staleness timeout set to %ds", seconds)

    def _on_transport_connect(self, name: str) -> None:
        """Called by a transport client on successful connection."""
        logger.info("Transport '%s' connected", name)
        # Simulator always supports latest protocol; BLE defaults to v1
        if name.startswith("sim"):
            self._transport_versions[name] = 2
        if self._observer:
            self._observer.on_connection_change(True, name)

    def _on_transport_disconnect(self, name: str) -> None:
        """Called by a transport client on disconnect."""
        logger.warning("Transport '%s' disconnected", name)
        if self._observer:
            self._observer.on_connection_change(False, name)

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

    async def _post_connect_sync(self, transport, name: str) -> None:
        """Run time sync, version read, and replay after a (re)connection."""
        await self._sync_time_for(transport)
        if hasattr(transport, 'read_version') and callable(getattr(transport, 'read_version', None)):
            try:
                version = await transport.read_version()
                if isinstance(version, int) and version >= 1:
                    self._transport_versions[name] = version
                    logger.info("Transport '%s': protocol version %d", name, version)
            except Exception:
                self._transport_versions[name] = 1
                logger.warning("Transport '%s': version read failed, defaulting to v1", name)
        await self._replay_active_for(transport, name)

    async def _replay_active_for(self, transport, name: str = "") -> None:
        """Replay all active notifications to a transport after reconnect."""
        logger.info("Replaying %d active notifications", len(self._active_notifications))
        for msg in list(self._active_notifications.values()):
            try:
                payload = daemon_message_to_ble_payload(msg)
            except ValueError:
                continue
            if payload is None:
                continue
            await transport.write_notification(payload)
            await asyncio.sleep(0.05)

        # Send current display state
        state = self._compute_display_state()
        self._last_display_state = state
        version = self._transport_versions.get(name, 1)
        if version >= 2:
            status_payload = display_state_to_ble_payload(state)
        else:
            status_payload = display_state_to_v1_payload(state)
        await transport.write_notification(status_payload)

    async def _transport_sender(self, name: str) -> None:
        """Process pending messages and send them over a named transport."""
        transport = self._transports[name]
        queue = self._transport_queues[name]
        # Initial connection — retries until connected
        await transport.ensure_connected()
        if transport.is_connected:
            await self._post_connect_sync(transport, name)
        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # Proactively reconnect if transport dropped
                if not transport.is_connected:
                    await transport.ensure_connected()
                    if transport.is_connected:
                        await self._post_connect_sync(transport, name)
                continue
            try:
                payload = daemon_message_to_ble_payload(msg)
            except ValueError:
                logger.error("[%s] Skipping unknown event: %s", name, msg.get("event"))
                continue
            if payload is None:
                continue

            was_connected = transport.is_connected
            await transport.ensure_connected()
            if not was_connected and transport.is_connected:
                await self._post_connect_sync(transport, name)

            success = await transport.write_notification(payload)

            if not success:
                was_connected = transport.is_connected
                await transport.ensure_connected()
                if not was_connected and transport.is_connected:
                    await self._post_connect_sync(transport, name)
                else:
                    await self._replay_active_for(transport, name)

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

        if hasattr(self, '_staleness_task'):
            self._staleness_task.cancel()
            try:
                await self._staleness_task
            except asyncio.CancelledError:
                pass

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

    async def add_transport(self, name: str, client: TransportClient) -> None:
        """Add a transport dynamically and start its sender task."""
        client._on_connect_cb = lambda: self._on_transport_connect(name)
        client._on_disconnect_cb = lambda: self._on_transport_disconnect(name)
        self._transports[name] = client
        self._transport_queues[name] = asyncio.Queue()
        self._sender_tasks[name] = asyncio.create_task(self._transport_sender(name))

    async def remove_transport(self, name: str) -> None:
        """Stop sender task, disconnect client, and remove transport."""
        if name in self._sender_tasks:
            self._sender_tasks[name].cancel()
            try:
                await self._sender_tasks[name]
            except asyncio.CancelledError:
                pass
            del self._sender_tasks[name]
        if name in self._transports:
            client = self._transports[name]
            if client.is_connected:
                await client.disconnect()
            del self._transports[name]
        self._transport_queues.pop(name, None)
        self._on_transport_disconnect(name)

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

        self._staleness_task = asyncio.create_task(self._staleness_checker())

        await self._shutdown_event.wait()
        logger.info("Daemon run() finished")


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
