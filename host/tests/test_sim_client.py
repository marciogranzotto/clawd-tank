# host/tests/test_sim_client.py
"""Tests for SimClient TCP transport."""

import asyncio
import json
import pytest
from clawd_tank_daemon.sim_client import SimClient


async def start_mock_server(handler):
    """Start a TCP server on an OS-assigned port. Returns (server, port)."""
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


@pytest.mark.asyncio
async def test_connect_and_write():
    received = []

    async def handler(reader, writer):
        while True:
            line = await reader.readline()
            if not line:
                break
            received.append(line.decode().strip())
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()
        assert client.is_connected

        payload = json.dumps({"action": "add", "id": "s1", "project": "p", "message": "m"})
        result = await client.write_notification(payload)
        assert result is True

        await client.disconnect()
        assert not client.is_connected
        await asyncio.sleep(0.05)

    assert len(received) == 1
    assert json.loads(received[0])["action"] == "add"


@pytest.mark.asyncio
async def test_read_config():
    async def handler(reader, writer):
        line = await reader.readline()
        req = json.loads(line.decode().strip())
        if req.get("action") == "read_config":
            writer.write(b'{"brightness":128,"sleep_timeout":300}\n')
            await writer.drain()
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()

        config = await client.read_config()
        assert config == {"brightness": 128, "sleep_timeout": 300}

        await client.disconnect()


@pytest.mark.asyncio
async def test_write_config():
    received = []

    async def handler(reader, writer):
        line = await reader.readline()
        if line:
            received.append(line.decode().strip())
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()

        result = await client.write_config(
            json.dumps({"brightness": 200})
        )
        assert result is True

        await client.disconnect()
        await asyncio.sleep(0.05)

    assert len(received) == 1
    parsed = json.loads(received[0])
    assert parsed["brightness"] == 200
    assert parsed["action"] == "write_config"


@pytest.mark.asyncio
async def test_write_after_server_close_returns_false():
    """After server closes connection, write should fail and mark disconnected."""
    async def handler(reader, writer):
        writer.close()
        await writer.wait_closed()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()
        await asyncio.sleep(0.1)  # Let server close propagate
        result = await client.write_notification('{"action":"clear"}')
        # Either write fails or next write will fail
        if result:
            await asyncio.sleep(0.05)
            result = await client.write_notification('{"action":"clear"}')
        assert result is False
        assert not client.is_connected


@pytest.mark.asyncio
async def test_connect_retries_on_refused():
    """connect() should retry when connection is refused."""
    # Find a port that is guaranteed free
    temp_server, port = await start_mock_server(lambda r, w: w.close())
    temp_server.close()
    await temp_server.wait_closed()

    client = SimClient(port=port, retry_interval=0.05)

    # Start server after a delay
    async def delayed_server():
        await asyncio.sleep(0.15)
        return await asyncio.start_server(
            lambda r, w: None,
            "127.0.0.1", port,
        )

    server_task = asyncio.create_task(delayed_server())
    connect_task = asyncio.create_task(client.connect())

    server = await server_task
    async with server:
        await asyncio.wait_for(connect_task, timeout=2.0)
        assert client.is_connected
        await client.disconnect()


@pytest.mark.asyncio
async def test_write_when_disconnected_returns_false():
    client = SimClient(port=1)  # Port doesn't matter, never connects
    result = await client.write_notification('{"action":"clear"}')
    assert result is False


@pytest.mark.asyncio
async def test_on_connect_cb_called():
    connected = []

    async def handler(reader, writer):
        await reader.read(1)  # wait for client to close
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port, on_connect_cb=lambda: connected.append(True))
        await client.connect()
        assert len(connected) == 1
        await client.disconnect()


@pytest.mark.asyncio
async def test_on_disconnect_cb_called():
    disconnected = []

    async def handler(reader, writer):
        writer.close()
        await writer.wait_closed()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(
            port=port,
            on_disconnect_cb=lambda: disconnected.append(True),
        )
        await client.connect()
        await asyncio.sleep(0.1)  # Let server close propagate
        result = await client.write_notification('{"action":"clear"}')
        if result:
            await asyncio.sleep(0.05)
            await client.write_notification('{"action":"clear"}')
        assert len(disconnected) >= 1


@pytest.mark.asyncio
async def test_send_command():
    """send_command sends arbitrary JSON payloads."""
    received = []
    async def handler(reader, writer):
        while True:
            line = await reader.readline()
            if not line: break
            received.append(line.decode().strip())
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()
        result = await client.send_command({"action": "show_window"})
        assert result is True
        await client.disconnect()
        await asyncio.sleep(0.05)
    assert len(received) == 1
    assert json.loads(received[0])["action"] == "show_window"


@pytest.mark.asyncio
async def test_send_command_pinned_bool():
    """set_window with bool True sends JSON boolean true."""
    received = []
    async def handler(reader, writer):
        while True:
            line = await reader.readline()
            if not line: break
            received.append(line.decode().strip())
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()
        await client.send_command({"action": "set_window", "pinned": True})
        await client.disconnect()
        await asyncio.sleep(0.05)
    parsed = json.loads(received[0])
    assert parsed["pinned"] is True  # JSON boolean, not int


@pytest.mark.asyncio
async def test_send_command_pinned_int():
    """set_window with int 1 (from preferences) sends JSON int 1.

    The simulator parser must accept both boolean true and integer 1
    as truthy for the pinned field, since preferences store bools as ints.
    """
    received = []
    async def handler(reader, writer):
        while True:
            line = await reader.readline()
            if not line: break
            received.append(line.decode().strip())
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()
        await client.send_command({"action": "set_window", "pinned": 1})
        await client.disconnect()
        await asyncio.sleep(0.05)
    parsed = json.loads(received[0])
    # int 1 is serialized as JSON number 1, not boolean true
    assert parsed["pinned"] == 1
    # Simulator C parser must handle this — see sim_socket.c:162

@pytest.mark.asyncio
async def test_background_reader_receives_events():
    """Background reader should invoke on_event callback for unsolicited messages."""
    events = []
    async def handler(reader, writer):
        await asyncio.sleep(0.1)
        writer.write(b'{"event":"window_hidden"}\n')
        await writer.drain()
        await asyncio.sleep(0.5)
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port, on_event_cb=lambda e: events.append(e))
        await client.connect()
        await asyncio.sleep(0.3)
        assert len(events) == 1
        assert events[0]["event"] == "window_hidden"
        await client.disconnect()

@pytest.mark.asyncio
async def test_read_config_with_background_reader():
    """read_config should still work when background reader is active."""
    async def handler(reader, writer):
        line = await reader.readline()
        req = json.loads(line.decode().strip())
        if req.get("action") == "read_config":
            writer.write(b'{"brightness":128,"sleep_timeout":300}\n')
            await writer.drain()
        await asyncio.sleep(0.5)
        writer.close()

    server, port = await start_mock_server(handler)
    async with server:
        client = SimClient(port=port)
        await client.connect()
        config = await client.read_config()
        assert config["brightness"] == 128
        await client.disconnect()
