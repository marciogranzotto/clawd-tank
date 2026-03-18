"""Simulator process lifecycle manager."""
import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
from typing import Callable, Optional

from .sim_client import SimClient, SIM_DEFAULT_PORT

logger = logging.getLogger("clawd-tank.sim-process")


class SimProcessManager:
    def __init__(self, port: int = SIM_DEFAULT_PORT, on_window_event: Optional[Callable] = None,
                 start_pinned: bool = False):
        self._port = port
        self._process: Optional[asyncio.subprocess.Process] = None
        self._client: Optional[SimClient] = None
        self._on_window_event = on_window_event
        self._start_pinned = start_pinned

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

    async def _kill_orphaned_sim(self) -> None:
        """Kill any orphaned clawd-tank-sim processes listening on our port."""
        def _do_kill():
            result = subprocess.run(
                ["lsof", "-ti", f":{self._port}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return
            for pid_str in result.stdout.strip().split('\n'):
                if not pid_str.strip():
                    continue
                pid = int(pid_str)
                # Verify it's actually a clawd-tank-sim process
                ps_result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "comm="],
                    capture_output=True, text=True, timeout=5,
                )
                if "clawd-tank-sim" not in ps_result.stdout:
                    logger.warning("PID %d on port %d is not clawd-tank-sim, skipping", pid, self._port)
                    continue
                logger.info("Killing orphaned clawd-tank-sim (PID %d) on port %d", pid, self._port)
                os.kill(pid, signal.SIGKILL)
        try:
            await asyncio.to_thread(_do_kill)
        except (subprocess.TimeoutExpired, ValueError, ProcessLookupError,
                PermissionError, FileNotFoundError, OSError):
            pass

    async def _log_stream(self, stream, level) -> None:
        if not stream:
            return
        try:
            async for line in stream:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.log(level, "%s", text)
        except (ValueError, asyncio.CancelledError):
            pass

    async def start(self) -> Optional[SimClient]:
        if await self._is_port_in_use():
            logger.warning("Port %d already in use, killing orphaned process", self._port)
            await self._kill_orphaned_sim()
            # Wait for port to be released, with timeout
            for _ in range(10):
                await asyncio.sleep(0.5)
                if not await self._is_port_in_use():
                    break
            else:
                logger.error("Port %d still in use after killing orphan", self._port)
                return None

        binary = self._find_binary()
        if not binary:
            logger.error("clawd-tank-sim binary not found")
            return None
        args = [binary, "--listen", str(self._port), "--hidden"]
        if self._start_pinned:
            args.append("--pinned")
        logger.info("Starting simulator: %s", " ".join(args))
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        asyncio.create_task(self._log_stream(self._process.stdout, logging.INFO))
        asyncio.create_task(self._log_stream(self._process.stderr, logging.WARNING))
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

    async def kill(self) -> None:
        """Immediately kill the simulator process without graceful shutdown."""
        if self._process and self._process.returncode is None:
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
