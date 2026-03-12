"""Tests for SocketServer: concurrent connections, malformed JSON, clean shutdown."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from clawd_daemon.socket_server import SocketServer


async def _send_raw(socket_path: Path, data: bytes) -> None:
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.write(data)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_socket_server_receives_message():
    """Server must deliver a well-formed JSON message to the callback."""
    received: list[dict] = []

    async def on_message(msg: dict) -> None:
        received.append(msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=on_message, socket_path=sock_path)
        await server.start()

        payload = {"event": "add", "session_id": "s1", "project": "p", "message": "m"}
        await _send_raw(sock_path, json.dumps(payload).encode())
        await asyncio.sleep(0.05)  # allow handler coroutine to run

        assert len(received) == 1
        assert received[0]["event"] == "add"
        assert received[0]["session_id"] == "s1"

        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_concurrent_connections():
    """Multiple simultaneous connections must each deliver their message."""
    received: list[dict] = []

    async def on_message(msg: dict) -> None:
        received.append(msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=on_message, socket_path=sock_path)
        await server.start()

        messages = [
            {"event": "add", "session_id": f"s{i}", "project": "p", "message": "m"}
            for i in range(5)
        ]
        await asyncio.gather(*[
            _send_raw(sock_path, json.dumps(m).encode()) for m in messages
        ])
        await asyncio.sleep(0.1)

        assert len(received) == 5
        session_ids = {r["session_id"] for r in received}
        assert session_ids == {"s0", "s1", "s2", "s3", "s4"}

        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_malformed_json_does_not_crash():
    """Invalid JSON must be silently absorbed — server must keep running."""
    received: list[dict] = []

    async def on_message(msg: dict) -> None:
        received.append(msg)

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=on_message, socket_path=sock_path)
        await server.start()

        # Send garbage
        await _send_raw(sock_path, b"not json {{{{")
        await asyncio.sleep(0.05)

        # Server must still accept a subsequent valid message
        payload = {"event": "dismiss", "session_id": "s1"}
        await _send_raw(sock_path, json.dumps(payload).encode())
        await asyncio.sleep(0.05)

        assert len(received) == 1
        assert received[0]["event"] == "dismiss"

        await server.stop()


@pytest.mark.asyncio
async def test_socket_server_stop_removes_socket_file():
    """stop() must clean up the socket file."""
    async def on_message(msg: dict) -> None:
        pass

    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test.sock"
        server = SocketServer(on_message=on_message, socket_path=sock_path)
        await server.start()
        assert sock_path.exists()

        await server.stop()
        assert not sock_path.exists()
