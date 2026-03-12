# host/tests/test_ble_config.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from clawd_tank_daemon.ble_client import ClawdBleClient, CONFIG_CHR_UUID


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
