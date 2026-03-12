"""BLE GATT client for communicating with the Clawd Tank ESP32 device."""

import asyncio
import json
import logging
from bleak import BleakClient, BleakScanner

logger = logging.getLogger("clawd-tank.ble")

SERVICE_UUID = "aecbefd9-98a2-4773-9fed-bb2166daa49a"
NOTIFICATION_CHR_UUID = "71ffb137-8b7a-47c9-9a7a-4b1b16662d9a"
CONFIG_CHR_UUID = "e9f6e626-5fca-4201-b80c-4d2b51c40f51"
SCAN_INTERVAL_SECS = 5


class ClawdBleClient:
    """Manages BLE connection to the Clawd Tank ESP32 device."""

    def __init__(self):
        self._client: BleakClient | None = None
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def connect(self) -> None:
        """Scan for and connect to the Clawd Tank device. Retries until found."""
        self._loop = asyncio.get_running_loop()
        while True:
            logger.info("Scanning for Clawd Tank device...")
            device = await BleakScanner.find_device_by_name(
                "Clawd Tank", timeout=SCAN_INTERVAL_SECS
            )
            if device is None:
                logger.debug("Clawd Tank not found, retrying...")
                continue

            logger.info("Found Clawd Tank: %s (%s)", device.name, device.address)
            try:
                client = BleakClient(
                    device,
                    disconnected_callback=self._on_disconnect,
                )
                await client.connect()
                self._client = client
                logger.info("Connected to Clawd Tank (MTU: %d)", client.mtu_size)
                return
            except Exception as e:
                logger.warning("Connection failed: %s, retrying...", e)
                await asyncio.sleep(SCAN_INTERVAL_SECS)

    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle disconnect — may be called from a non-event-loop thread."""
        logger.warning("Disconnected from Clawd Tank")
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._clear_client)
        else:
            self._clear_client()

    def _clear_client(self) -> None:
        self._client = None

    async def ensure_connected(self) -> None:
        """Reconnect if disconnected."""
        if not self.is_connected:
            await self.connect()

    async def write_notification(self, payload: str) -> bool:
        """Write a JSON payload to the notification characteristic.

        Returns True on success, False on failure.
        """
        async with self._lock:
            if not self.is_connected:
                logger.warning("Not connected, cannot write")
                return False
            try:
                data = payload.encode("utf-8")
                await self._client.write_gatt_char(
                    NOTIFICATION_CHR_UUID, data, response=False
                )
                logger.debug("Wrote %d bytes to BLE", len(data))
                return True
            except Exception as e:
                logger.error("BLE write failed: %s", e)
                return False

    async def read_config(self) -> dict:
        """Read full device config from the config characteristic.

        Returns empty dict if not connected or on error.
        """
        async with self._lock:
            if not self.is_connected:
                logger.warning("Not connected, cannot read config")
                return {}
            try:
                data = await self._client.read_gatt_char(CONFIG_CHR_UUID)
                return json.loads(data.decode("utf-8"))
            except Exception as e:
                logger.error("Config read failed: %s", e)
                return {}

    async def write_config(self, payload: str) -> bool:
        """Write a partial config JSON to the config characteristic.

        Returns True on success, False on failure.
        """
        async with self._lock:
            if not self.is_connected:
                logger.warning("Not connected, cannot write config")
                return False
            try:
                data = payload.encode("utf-8")
                await self._client.write_gatt_char(
                    CONFIG_CHR_UUID, data, response=False
                )
                logger.debug("Config write: %s", payload)
                return True
            except Exception as e:
                logger.error("Config write failed: %s", e)
                return False

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
