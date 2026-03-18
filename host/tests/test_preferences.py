"""Tests for preferences read-modify-write and defaults merging."""
import json
import pytest
from pathlib import Path
from clawd_tank_menubar.preferences import load_preferences, save_preferences, DEFAULTS

@pytest.fixture
def prefs_file(tmp_path):
    return tmp_path / "preferences.json"

def test_load_returns_defaults_when_missing(prefs_file):
    result = load_preferences(prefs_file)
    assert result == DEFAULTS

def test_load_merges_missing_keys_with_defaults(prefs_file):
    prefs_file.write_text(json.dumps({"sim_enabled": False}))
    result = load_preferences(prefs_file)
    assert result["sim_enabled"] is False
    assert result["ble_enabled"] == DEFAULTS["ble_enabled"]
    assert result["sim_window_visible"] == DEFAULTS["sim_window_visible"]
    assert result["sim_always_on_top"] == DEFAULTS["sim_always_on_top"]

def test_save_preserves_existing_keys(prefs_file):
    prefs_file.write_text(json.dumps({"sim_enabled": True, "ble_enabled": False}))
    save_preferences(path=prefs_file, updates={"sim_window_visible": False})
    result = json.loads(prefs_file.read_text())
    assert result["sim_enabled"] is True
    assert result["ble_enabled"] is False
    assert result["sim_window_visible"] is False

def test_save_creates_file_if_missing(prefs_file):
    save_preferences(path=prefs_file, updates={"ble_enabled": False})
    result = json.loads(prefs_file.read_text())
    assert result["ble_enabled"] is False
    loaded = load_preferences(prefs_file)
    assert loaded["sim_enabled"] == DEFAULTS["sim_enabled"]


def test_int_preference_is_truthy(prefs_file):
    """Preferences saved by rumps use int 0/1, not bool.
    Code using prefs.get("key", True) must treat int 1 as truthy.
    This documents the known behavior — see sim_socket.c cJSON fix."""
    prefs_file.write_text(json.dumps({"sim_always_on_top": 1}))
    result = load_preferences(prefs_file)
    assert result["sim_always_on_top"]  # int 1 is truthy
    assert result["sim_always_on_top"] == 1
    assert result["sim_always_on_top"] is not True  # but it's int, not bool
