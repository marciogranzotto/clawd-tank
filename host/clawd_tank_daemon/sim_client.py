# host/clawd_tank_daemon/sim_client.py
"""TCP transport client for connecting to the Clawd Tank simulator."""

import asyncio
import json
import logging

logger = logging.getLogger("clawd-tank.sim")

SIM_DEFAULT_PORT = 19872
SIM_RETRY_INTERVAL = 5  # seconds


class SimClient:
    """TCP client that connects to the simulator's TCP listener."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = SIM_DEFAULT_PORT,
        on_disconnect_cb=None,
        on_connect_cb=None,
        retry_interval: float = SIM_RETRY_INTERVAL,
    ):
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._on_disconnect_cb = on_disconnect_cb
        self._on_connect_cb = on_connect_cb
        self._retry_interval = retry_interval
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """Connect to the simulator. Retries until successful."""
        if self._writer is not None:
            await self.disconnect()
        while True:
            try:
                logger.info("Connecting to simulator at %s:%d...", self._host, self._port)
                self._reader, self._writer = await asyncio.open_connection(
                    self._host, self._port
                )
                logger.info("Connected to simulator")
                if self._on_connect_cb:
                    self._on_connect_cb()
                return
            except (ConnectionRefusedError, OSError) as e:
                logger.debug("Simulator not available: %s, retrying...", e)
                await asyncio.sleep(self._retry_interval)

    async def disconnect(self) -> None:
        """Close the TCP connection."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._writer = None
        self._reader = None

    async def ensure_connected(self) -> None:
        """Reconnect if disconnected."""
        if not self.is_connected:
            await self.connect()

    async def write_notification(self, payload: str) -> bool:
        """Send a JSON payload followed by newline. Returns True on success."""
        async with self._lock:
            if not self.is_connected:
                logger.warning("Not connected to simulator, cannot write")
                return False
            try:
                self._writer.write((payload + "\n").encode("utf-8"))
                await self._writer.drain()
                return True
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                logger.error("Simulator write failed: %s", e)
                self._handle_disconnect()
                return False

    async def read_config(self) -> dict:
        """Request and read config from simulator. Returns empty dict on error."""
        async with self._lock:
            if not self.is_connected:
                return {}
            try:
                self._writer.write(b'{"action":"read_config"}\n')
                await self._writer.drain()
                line = await asyncio.wait_for(self._reader.readline(), timeout=2.0)
                if not line:
                    self._handle_disconnect()
                    return {}
                return json.loads(line.decode("utf-8").strip())
            except (asyncio.TimeoutError, json.JSONDecodeError, OSError) as e:
                logger.error("Config read failed: %s", e)
                self._handle_disconnect()
                return {}

    async def write_config(self, payload: str) -> bool:
        """Send a config write payload. Returns True on success."""
        return await self.write_notification(payload)

    def _handle_disconnect(self) -> None:
        """Clean up state and notify on disconnect."""
        self._writer = None
        self._reader = None
        if self._on_disconnect_cb:
            self._on_disconnect_cb()
