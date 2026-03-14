"""Tests for SimProcessManager."""
import asyncio
import os
import sys
import pytest
from unittest.mock import patch
from clawd_tank_daemon.sim_process import SimProcessManager

def test_find_binary_in_app_bundle():
    mgr = SimProcessManager()
    with patch.object(os.path, "isfile", return_value=True):
        with patch("sys.executable", "/App.app/Contents/MacOS/python"):
            path = mgr._find_binary()
            assert path == "/App.app/Contents/MacOS/clawd-tank-sim"

def test_find_binary_fallback_to_which():
    mgr = SimProcessManager()
    with patch.object(os.path, "isfile", return_value=False):
        with patch("shutil.which", return_value="/usr/local/bin/clawd-tank-sim"):
            path = mgr._find_binary()
            assert path == "/usr/local/bin/clawd-tank-sim"

def test_find_binary_returns_none():
    mgr = SimProcessManager()
    with patch.object(os.path, "isfile", return_value=False):
        with patch("shutil.which", return_value=None):
            path = mgr._find_binary()
            assert path is None

@pytest.mark.asyncio
async def test_port_probe_detects_existing():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        mgr = SimProcessManager(port=port)
        assert await mgr._is_port_in_use() is True
    mgr2 = SimProcessManager(port=port)
    assert await mgr2._is_port_in_use() is False

def test_on_window_event_callback():
    events = []
    mgr = SimProcessManager(on_window_event=lambda e: events.append(e))
    mgr._handle_sim_event({"event": "window_hidden"})
    assert len(events) == 1
    assert events[0]["event"] == "window_hidden"
