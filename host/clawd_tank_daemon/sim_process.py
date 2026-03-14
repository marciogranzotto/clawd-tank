"""Simulator process lifecycle manager."""
import asyncio
import logging
import os
import shutil
import signal
import sys
from typing import Callable, Optional

from .sim_client import SimClient, SIM_DEFAULT_PORT

logger = logging.getLogger("clawd-tank.sim-process")


class SimProcessManager:
    def __init__(self, port: int = SIM_DEFAULT_PORT, on_window_event: Optional[Callable] = None):
        self._port = port
        self._process: Optional[asyncio.subprocess.Process] = None
        self._client: Optional[SimClient] = None
        self._on_window_event = on_window_event

    def _find_binary(self) -> Optional[str]:
        # 1. Next to sys.executable (inside .app bundle)
        exe_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(exe_dir, "clawd-tank-sim")
        if os.path.isfile(candidate):
            return candidate
        # 2. NSBundle path (py2app)
        try:
            from Foundation import NSBundle
            bundle = NSBundle.mainBundle()
            if bundle:
                bundle_candidate = os.path.join(bundle.bundlePath(), "Contents", "MacOS", "clawd-tank-sim")
                if os.path.isfile(bundle_candidate):
                    return bundle_candidate
        except ImportError:
            pass
        # 3. PATH lookup (development)
        return shutil.which("clawd-tank-sim")

    async def _is_port_in_use(self) -> bool:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", self._port), timeout=1.0)
            writer.close()
            await writer.wait_closed()
            return True
        except (ConnectionRefusedError, OSError, asyncio.TimeoutError):
            return False

    def _handle_sim_event(self, event: dict) -> None:
        if self._on_window_event:
            self._on_window_event(event)

    async def _log_stderr(self) -> None:
        if not self._process or not self._process.stderr:
            return
        try:
            async for line in self._process.stderr:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.warning("[sim-stderr] %s", text)
        except (ValueError, asyncio.CancelledError):
            pass

    async def start(self) -> Optional[SimClient]:
        if await self._is_port_in_use():
            logger.warning("Port %d already in use, connecting to existing simulator", self._port)
        else:
            binary = self._find_binary()
            if not binary:
                logger.error("clawd-tank-sim binary not found")
                return None
            logger.info("Starting simulator: %s --listen %d --hidden", binary, self._port)
            self._process = await asyncio.create_subprocess_exec(
                binary, "--listen", str(self._port), "--hidden",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            asyncio.create_task(self._log_stderr())
            await asyncio.sleep(0.3)
        self._client = SimClient(port=self._port, on_event_cb=self._handle_sim_event)
        return self._client

    async def stop(self) -> None:
        if self._client:
            await self._client.disconnect()
            self._client = None
        if self._process and self._process.returncode is None:
            logger.info("Stopping simulator process (PID %d)", self._process.pid)
            self._process.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("Simulator did not exit, sending SIGKILL")
                self._process.kill()
                await self._process.wait()
            self._process = None

    async def show_window(self) -> bool:
        if self._client and self._client.is_connected:
            return await self._client.send_command({"action": "show_window"})
        return False

    async def hide_window(self) -> bool:
        if self._client and self._client.is_connected:
            return await self._client.send_command({"action": "hide_window"})
        return False

    async def set_pinned(self, pinned: bool) -> bool:
        if self._client and self._client.is_connected:
            return await self._client.send_command({"action": "set_window", "pinned": pinned})
        return False

    @property
    def is_running(self) -> bool:
        if self._process is None:
            return False
        return self._process.returncode is None
