# host/clawd_tank_menubar/app.py
"""Clawd Tank macOS status bar application."""

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

import rumps

from clawd_tank_daemon.daemon import ClawdDaemon, DaemonObserver
from clawd_tank_daemon.sim_client import SimClient, SIM_DEFAULT_PORT
from . import hooks, launchd
from .preferences import load_preferences, save_preferences
from .slider import create_slider_menu_item

logger = logging.getLogger("clawd-tank.menubar")

SESSION_TIMEOUT_OPTIONS = [
    ("1 minute", 60),
    ("2 minutes", 120),
    ("5 minutes", 300),
    ("10 minutes", 600),
    ("30 minutes", 1800),
    ("Never", 0),
]


class ClawdTankApp(rumps.App, DaemonObserver):
    def __init__(self):
        super().__init__("Clawd Tank", quit_button=None)

        self._daemon: Optional[ClawdDaemon] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_ready = threading.Event()
        self._transport_status: dict[str, bool] = {}
        self._notification_count = 0
        self._current_config: dict = {}
        self._sim_process = None

        prefs = load_preferences()

        # --- BLE submenu ---
        self._ble_menu = rumps.MenuItem("BLE  \u25CB Disabled")
        self._ble_status = rumps.MenuItem("Status: Initializing...")
        self._ble_status.set_callback(None)
        self._ble_enabled_toggle = rumps.MenuItem(
            "Enabled", callback=self._on_toggle_ble_enabled
        )
        self._ble_enabled_toggle.state = prefs.get("ble_enabled", True)
        self._ble_reconnect = rumps.MenuItem("Reconnect", callback=None)
        self._ble_menu.update([
            self._ble_status,
            None,
            self._ble_enabled_toggle,
            None,
            self._ble_reconnect,
        ])

        # --- Simulator submenu ---
        self._sim_menu = rumps.MenuItem("Simulator  \u25CB Disabled")
        self._sim_status = rumps.MenuItem("Status: Initializing...")
        self._sim_status.set_callback(None)
        self._sim_enabled_toggle = rumps.MenuItem(
            "Enabled", callback=self._on_toggle_sim_enabled
        )
        self._sim_enabled_toggle.state = prefs.get("sim_enabled", True)
        self._sim_window_toggle = rumps.MenuItem(
            "Show Window", callback=None
        )
        self._sim_window_toggle.state = prefs.get("sim_window_visible", True)
        self._sim_pinned_toggle = rumps.MenuItem(
            "Always on Top", callback=None
        )
        self._sim_pinned_toggle.state = prefs.get("sim_always_on_top", True)
        self._sim_menu.update([
            self._sim_status,
            None,
            self._sim_enabled_toggle,
            None,
            self._sim_window_toggle,
            self._sim_pinned_toggle,
        ])

        # Brightness slider — rumps MenuItem with custom NSView
        self._brightness_slider = create_slider_menu_item(
            "Brightness", min_val=0, max_val=255, initial=102,
            on_change=self._on_brightness_change,
        )
        self._brightness_item = rumps.MenuItem("Brightness")
        self._brightness_item._menuitem.setView_(self._brightness_slider.view)

        # Session timeout submenu
        self._session_timeout_menu = rumps.MenuItem("Session Timeout")
        self._session_timeout_value = 300
        for label, seconds in SESSION_TIMEOUT_OPTIONS:
            item = rumps.MenuItem(label, callback=self._on_session_timeout_select)
            item._seconds = seconds
            if seconds == 300:
                item.state = True
            self._session_timeout_menu.add(item)

        # Claude Code hooks
        self._hooks_item = rumps.MenuItem(
            "Install Claude Code Hooks",
            callback=self._on_install_hooks,
        )
        self._hooks_item.state = hooks.are_hooks_installed()

        # Launch at login
        self._login_item = rumps.MenuItem(
            "Launch at Login",
            callback=self._on_toggle_login,
        )
        self._login_item.state = launchd.is_enabled()

        # Quit
        self._quit_item = rumps.MenuItem("Quit Clawd Tank", callback=self._on_quit)

        # Assemble menu
        self.menu = [
            self._ble_menu,
            self._sim_menu,
            None,
            self._brightness_item,
            self._session_timeout_menu,
            None,
            self._hooks_item,
            self._login_item,
            None,
            self._quit_item,
        ]

        # Set initial icon and hide text title so only the icon shows in the menu bar
        self.icon = self._icon_path("crab-disconnected")
        self.template = True
        self.title = ""
        self._update_menu_state()

    @property
    def _connected(self) -> bool:
        return any(self._transport_status.values()) if self._transport_status else False

    # --- Lifecycle ---

    def _start_daemon_thread(self):
        """Start the daemon's asyncio event loop in a background thread."""
        self._daemon = ClawdDaemon(observer=self, headless=False)

        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop_ready.set()
            self._loop.run_until_complete(self._daemon.run())

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        self._loop_ready.wait(timeout=5)

        # Create transports based on preferences
        prefs = load_preferences()

        if prefs.get("ble_enabled", True):
            from clawd_tank_daemon.ble_client import ClawdBleClient
            client = ClawdBleClient()
            self._transport_status["ble"] = False
            asyncio.run_coroutine_threadsafe(
                self._daemon.add_transport("ble", client), self._loop
            )

        if prefs.get("sim_enabled", True):
            self._start_simulator()

    # --- DaemonObserver callbacks (called from asyncio thread) ---

    def on_connection_change(self, connected: bool, transport: str = "") -> None:
        if transport:
            self._transport_status[transport] = connected
        if connected and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._read_device_config(), self._loop
            )
        self._schedule_menu_update()

    def on_notification_change(self, count: int) -> None:
        self._notification_count = count
        self._schedule_menu_update()

    # --- Config ---

    async def _read_device_config(self):
        """Read config from device and update menu."""
        if self._daemon:
            config = await self._daemon.read_config()
            if config:
                self._current_config = config
                self._schedule_menu_update()

    def _schedule_menu_update(self):
        """Thread-safe menu update via PyObjC main thread dispatch."""
        try:
            from PyObjCTools.AppHelper import callAfter
            callAfter(self._update_menu_state)
        except ImportError:
            self._update_menu_state()

    def _update_menu_state(self):
        """Update all menu items based on current state. Must run on main thread."""
        connected = self._connected

        # --- BLE submenu state ---
        if not self._ble_enabled_toggle.state:
            self._ble_menu.title = "BLE  \u25CB Disabled"
            self._ble_status.title = "Status: Disabled"
            self._ble_reconnect.set_callback(None)
        else:
            ble_connected = self._transport_status.get("ble", False)
            if ble_connected:
                self._ble_menu.title = "BLE  \u25CF Connected"
                self._ble_status.title = "Status: Connected"
            else:
                self._ble_menu.title = "BLE  \u25CF Connecting..."
                self._ble_status.title = "Status: Connecting..."
            self._ble_reconnect.set_callback(self._on_reconnect)

        # --- Simulator submenu state ---
        if not self._sim_enabled_toggle.state:
            self._sim_menu.title = "Simulator  \u25CB Disabled"
            self._sim_status.title = "Status: Disabled"
            self._sim_window_toggle.set_callback(None)
            self._sim_pinned_toggle.set_callback(None)
        else:
            sim_connected = self._transport_status.get("sim", False)
            if sim_connected:
                self._sim_menu.title = "Simulator  \u25CF Running"
                self._sim_status.title = "Status: Running"
            else:
                self._sim_menu.title = "Simulator  \u25CF Connecting..."
                self._sim_status.title = "Status: Connecting..."
            self._sim_window_toggle.set_callback(self._on_toggle_sim_window)
            self._sim_pinned_toggle.set_callback(self._on_toggle_sim_pinned)

        # --- Icon and global state ---
        if connected:
            if self._notification_count > 0:
                self.icon = self._icon_path("crab-notifications")
            else:
                self.icon = self._icon_path("crab-connected")

            brightness = self._current_config.get("brightness", 102)
            self._brightness_slider.set_value(brightness)
            self._brightness_slider.set_enabled(True)

            timeout = self._current_config.get("sleep_timeout", 300)
            self._session_timeout_value = timeout
            for key, item in self._session_timeout_menu.items():
                item.state = (item._seconds == timeout)
        else:
            self.icon = self._icon_path("crab-disconnected")
            self._brightness_slider.set_enabled(False)
        self.title = ""

    def _icon_path(self, name: str) -> Optional[str]:
        """Return path to icon file, or None if not found."""
        import importlib.resources
        try:
            icons_dir = importlib.resources.files("clawd_tank_menubar") / "icons"
            path = icons_dir / f"{name}.png"
            if hasattr(path, '__fspath__'):
                return str(path)
        except Exception:
            pass
        return None

    # --- Menu callbacks ---

    def _on_brightness_change(self, value: int):
        """Called from slider on main thread. Send config write via asyncio."""
        if self._loop and self._connected:
            payload = json.dumps({"brightness": value})
            asyncio.run_coroutine_threadsafe(
                self._daemon.write_config(payload), self._loop
            )

    def _on_session_timeout_select(self, sender):
        seconds = sender._seconds
        self._session_timeout_value = seconds

        for key, item in self._session_timeout_menu.items():
            item.state = (item._seconds == seconds)

        if self._loop and self._connected:
            payload = json.dumps({"sleep_timeout": seconds})
            asyncio.run_coroutine_threadsafe(
                self._daemon.write_config(payload), self._loop
            )

            # Update daemon staleness timeout
            if self._daemon:
                self._daemon.set_session_timeout(seconds)

    def _on_toggle_ble_enabled(self, sender):
        """Toggle BLE transport on/off."""
        sender.state = not sender.state
        save_preferences(updates={"ble_enabled": sender.state})

        if sender.state:
            # Enable: create BLE client and add transport
            from clawd_tank_daemon.ble_client import ClawdBleClient
            client = ClawdBleClient()
            self._transport_status["ble"] = False
            self._schedule_menu_update()
            if self._loop and self._daemon:
                asyncio.run_coroutine_threadsafe(
                    self._daemon.add_transport("ble", client), self._loop
                )
        else:
            # Disable: remove transport
            self._transport_status.pop("ble", None)
            self._schedule_menu_update()
            if self._loop and self._daemon:
                asyncio.run_coroutine_threadsafe(
                    self._daemon.remove_transport("ble"), self._loop
                )

    def _on_toggle_sim_enabled(self, sender):
        """Toggle simulator transport on/off."""
        sender.state = not sender.state
        save_preferences(updates={"sim_enabled": sender.state})

        if sender.state:
            self._start_simulator()
            self._schedule_menu_update()
        else:
            self._stop_simulator()
            self._schedule_menu_update()

    def _on_toggle_sim_window(self, sender):
        """Toggle simulator window visibility."""
        sender.state = not sender.state
        save_preferences(updates={"sim_window_visible": sender.state})

        if self._sim_process and self._loop:
            if sender.state:
                asyncio.run_coroutine_threadsafe(
                    self._sim_process.show_window(), self._loop
                )
            else:
                asyncio.run_coroutine_threadsafe(
                    self._sim_process.hide_window(), self._loop
                )

    def _on_toggle_sim_pinned(self, sender):
        """Toggle simulator always-on-top."""
        sender.state = not sender.state
        save_preferences(updates={"sim_always_on_top": sender.state})

        if self._sim_process and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._sim_process.set_pinned(sender.state), self._loop
            )

    def _on_sim_window_event(self, event: dict):
        """Handle events from the simulator process (e.g. window_hidden)."""
        event_type = event.get("event")
        if event_type == "window_hidden":
            self._sim_window_toggle.state = False
            save_preferences(updates={"sim_window_visible": False})
            self._schedule_menu_update()

    def _on_install_hooks(self, sender):
        was_installed = hooks.are_hooks_installed()
        hooks.install_notify_script()
        hooks.install_hooks()
        sender.state = True
        if was_installed:
            rumps.alert(
                title="Hooks Updated",
                message="Claude Code hooks have been updated. "
                        "Restart your Claude Code sessions for the changes to take effect.",
            )
        else:
            rumps.alert(
                title="Hooks Installed",
                message="Claude Code hooks have been added to ~/.claude/settings.json. "
                        "Restart your Claude Code sessions for the hooks to take effect.",
            )

    def _on_toggle_login(self, sender):
        if launchd.is_enabled():
            launchd.disable()
        else:
            launchd.enable()
        sender.state = launchd.is_enabled()

    def _on_reconnect(self, _):
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._daemon.reconnect(), self._loop
            )

    # --- Simulator lifecycle ---

    def _start_simulator(self):
        """Start the simulator process and add it as a transport."""
        from clawd_tank_daemon.sim_process import SimProcessManager
        self._sim_process = SimProcessManager(on_window_event=self._on_sim_window_event)
        self._transport_status["sim"] = False

        async def _do_start():
            client = await self._sim_process.start()
            if client:
                await self._daemon.add_transport("sim", client)
                prefs = load_preferences()
                if prefs.get("sim_window_visible", True):
                    await self._sim_process.show_window()
                await self._sim_process.set_pinned(prefs.get("sim_always_on_top", True))

        if self._loop and self._daemon:
            asyncio.run_coroutine_threadsafe(_do_start(), self._loop)

    def _stop_simulator(self):
        """Stop the simulator process and remove it as a transport."""
        async def _do_stop():
            await self._daemon.remove_transport("sim")
            if self._sim_process:
                await self._sim_process.stop()
                self._sim_process = None
            self._transport_status.pop("sim", None)

        if self._loop and self._daemon:
            asyncio.run_coroutine_threadsafe(_do_stop(), self._loop)

    def _on_quit(self, _):
        try:
            if self._loop and self._daemon:
                if self._sim_process:
                    asyncio.run_coroutine_threadsafe(
                        self._sim_process.stop(), self._loop
                    )
                future = asyncio.run_coroutine_threadsafe(
                    self._daemon._shutdown(), self._loop
                )
                future.result(timeout=5)
            rumps.quit_application()
        except Exception:
            logger.exception("Error during quit, force-killing")
            os._exit(1)


def main():
    log_dir = Path.home() / "Library" / "Logs" / "ClawdTank"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "clawd-tank.log"),
        ],
    )
    hooks.install_notify_script()
    app = ClawdTankApp()
    app._start_daemon_thread()
    app.run()


if __name__ == "__main__":
    main()
