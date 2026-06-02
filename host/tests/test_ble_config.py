# host/tests/test_ble_config.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from clawd_tank_daemon.ble_client import (
    ClawdBleClient,
    CONFIG_CHR_UUID,
    VERSION_CHR_UUID,
)


@pytest.mark.asyncio
async def test_read_config_returns_dict():
    client = ClawdBleClient()
    client._client = MagicMock()
    client._client.is_connected = True
    client._client.read_gatt_char = AsyncMock(
        return_value=b'{"brightness":102,"sleep_timeout":300}'
    )
    result = await client.read_config()
    assert result == {"brightness": 102, "sleep_timeout": 300}
    client._client.read_gatt_char.assert_called_once_with(CONFIG_CHR_UUID)


@pytest.mark.asyncio
async def test_read_config_not_connected():
    client = ClawdBleClient()
    client._client = None
    result = await client.read_config()
    assert result == {}


@pytest.mark.asyncio
async def test_write_config_success():
    client = ClawdBleClient()
    client._client = MagicMock()
    client._client.is_connected = True
    client._client.write_gatt_char = AsyncMock()
    result = await client.write_config('{"brightness":200}')
    assert result is True
    client._client.write_gatt_char.assert_called_once_with(
        CONFIG_CHR_UUID, b'{"brightness":200}', response=False
    )


@pytest.mark.asyncio
async def test_write_config_not_connected():
    client = ClawdBleClient()
    client._client = None
    result = await client.write_config('{"brightness":200}')
    assert result is False


@pytest.mark.asyncio
async def test_write_config_ble_error():
    client = ClawdBleClient()
    client._client = MagicMock()
    client._client.is_connected = True
    client._client.write_gatt_char = AsyncMock(side_effect=Exception("BLE error"))
    result = await client.write_config('{"brightness":200}')
    assert result is False


# --- Active liveness probe (ping) ---
# Regression: on macOS CoreBluetooth the disconnect callback often does NOT fire
# on range/sleep link loss, and notification writes use response=False so a dead
# link never raises. is_connected then stays stale-True and the sender's reconnect
# branch is never taken. ping() is the active round-trip probe that surfaces the
# dead link so the sender re-scans.

@pytest.mark.asyncio
async def test_ping_returns_true_when_link_alive():
    client = ClawdBleClient()
    client._client = MagicMock()
    client._client.is_connected = True
    client._client.read_gatt_char = AsyncMock(return_value=b"2")
    result = await client.ping()
    assert result is True
    client._client.read_gatt_char.assert_called_once_with(VERSION_CHR_UUID)
    # Link is alive — client must NOT be dropped.
    assert client._client is not None


@pytest.mark.asyncio
async def test_ping_not_connected_returns_false():
    client = ClawdBleClient()
    client._client = None
    result = await client.ping()
    assert result is False


@pytest.mark.asyncio
async def test_ping_failure_drops_client_and_notifies():
    """A failed probe must clear the client (so is_connected -> False) and fire
    the disconnect callback, which is what triggers the sender to re-scan."""
    disconnect_calls = []
    client = ClawdBleClient(on_disconnect_cb=lambda: disconnect_calls.append(True))
    underlying = MagicMock()
    underlying.is_connected = True
    underlying.read_gatt_char = AsyncMock(side_effect=Exception("link dead"))
    underlying.disconnect = AsyncMock()
    client._client = underlying

    result = await client.ping()

    assert result is False
    assert client._client is None          # client dropped -> is_connected False
    assert client.is_connected is False
    assert disconnect_calls == [True]      # observer notified
