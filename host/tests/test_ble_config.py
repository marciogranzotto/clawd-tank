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


@pytest.mark.asyncio
async def test_ping_times_out_and_drops_client(monkeypatch):
    """A read that hangs on a stale-connected dead link must NOT block forever.
    The bounded timeout makes ping() fail fast, drop the client, and let the
    sender reconnect — without holding _lock for bleak's full default timeout."""
    import clawd_tank_daemon.ble_client as bc
    monkeypatch.setattr(bc, "GATT_READ_TIMEOUT_SECS", 0.05)
    client = ClawdBleClient()
    underlying = MagicMock()
    underlying.is_connected = True

    async def slow_read(uuid):
        await asyncio.sleep(1.0)  # longer than the patched timeout

    underlying.read_gatt_char = slow_read
    underlying.disconnect = AsyncMock()
    client._client = underlying

    result = await client.ping()

    assert result is False
    assert client._client is None


@pytest.mark.asyncio
async def test_read_version_returns_one_when_not_connected():
    client = ClawdBleClient()
    client._client = None
    assert await client.read_version() == 1


@pytest.mark.asyncio
async def test_read_version_parses_numeric_payload():
    client = ClawdBleClient()
    client._client = MagicMock()
    client._client.is_connected = True
    client._client.read_gatt_char = AsyncMock(return_value=b"2")
    assert await client.read_version() == 2


@pytest.mark.asyncio
async def test_read_version_non_numeric_payload_returns_one_without_drop():
    """A successful read of a non-numeric payload means v1 firmware — the link
    is alive, so the client must NOT be dropped."""
    client = ClawdBleClient()
    underlying = MagicMock()
    underlying.is_connected = True
    underlying.read_gatt_char = AsyncMock(return_value=b"notanumber")
    underlying.disconnect = AsyncMock()
    client._client = underlying
    assert await client.read_version() == 1
    assert client._client is underlying  # link alive, not dropped


@pytest.mark.asyncio
async def test_read_version_read_failure_drops_client():
    client = ClawdBleClient()
    underlying = MagicMock()
    underlying.is_connected = True
    underlying.read_gatt_char = AsyncMock(side_effect=Exception("link dead"))
    underlying.disconnect = AsyncMock()
    client._client = underlying
    assert await client.read_version() == 1
    assert client._client is None  # read failure dropped the client


@pytest.mark.asyncio
async def test_read_version_is_lock_guarded():
    """read_version reads the same characteristic as ping(); both must hold
    _lock so they never issue overlapping reads (bleak keys read futures by
    characteristic handle, so a concurrent read clobbers the in-flight one)."""
    client = ClawdBleClient()
    underlying = MagicMock()
    underlying.is_connected = True
    underlying.read_gatt_char = AsyncMock(return_value=b"2")
    client._client = underlying

    await client._lock.acquire()
    task = asyncio.create_task(client.read_version())
    await asyncio.sleep(0.01)
    assert not task.done()  # blocked on the lock held by us
    client._lock.release()
    assert await task == 2


@pytest.mark.asyncio
async def test_disconnect_notified_only_once_per_connection():
    """The proactive drop (_handle_disconnect) and bleak's own disconnect
    callback (_on_disconnect) can both fire for the same physical disconnect;
    the daemon must be notified only once."""
    calls = []
    client = ClawdBleClient(on_disconnect_cb=lambda: calls.append(True))
    underlying = MagicMock()
    underlying.is_connected = True
    underlying.disconnect = AsyncMock()
    client._client = underlying

    await client._handle_disconnect()      # proactive drop notifies
    client._on_disconnect(underlying)      # bleak's late callback for same drop

    assert calls == [True]                 # collapsed to a single notification


@pytest.mark.asyncio
async def test_disconnect_notified_resets_on_new_connection():
    """A fresh connection re-arms the guard so the next disconnect notifies."""
    calls = []
    client = ClawdBleClient(on_disconnect_cb=lambda: calls.append(True))
    u1 = MagicMock()
    u1.is_connected = True
    u1.disconnect = AsyncMock()
    client._client = u1

    await client._handle_disconnect()
    assert calls == [True]

    client._disconnect_notified = False    # connect() does this on success
    u2 = MagicMock()
    u2.is_connected = True
    u2.disconnect = AsyncMock()
    client._client = u2

    await client._handle_disconnect()
    assert calls == [True, True]
