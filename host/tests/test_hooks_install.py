"""Tests for additive (non-clobbering) hook installation into Claude settings.

install_hooks() must MERGE Clawd Tank's hooks into the user's existing
Claude Code settings without removing the user's own hooks, and must be
idempotent (re-running never duplicates our entries). are_hooks_installed()
must be matcher-aware so a new/changed matcher is detected as "outdated".
"""

import json

import pytest

from clawd_tank_menubar import hooks
from clawd_tank_menubar.hooks import (
    HOOKS_CONFIG,
    HOOK_COMMAND,
    are_hooks_installed,
    install_hooks,
)


@pytest.fixture(autouse=True)
def settings_path(tmp_path, monkeypatch):
    """Redirect the Claude settings path to a temp file for every test."""
    p = tmp_path / "settings.json"
    monkeypatch.setattr(hooks, "CLAUDE_SETTINGS_PATH", p)
    return p


def _read(settings_path) -> dict:
    return json.loads(settings_path.read_text())


def _commands_for(settings: dict, event: str) -> list[str]:
    """Flatten every hook command registered for an event across all groups."""
    cmds = []
    for group in settings.get("hooks", {}).get(event, []):
        for h in group.get("hooks", []):
            cmds.append(h.get("command", ""))
    return cmds


def _our_command_count(settings: dict, event: str) -> int:
    return sum(1 for c in _commands_for(settings, event) if HOOK_COMMAND in c)


# --- Fresh install ---


def test_install_creates_settings_when_absent(settings_path):
    assert not settings_path.exists()
    install_hooks()
    assert settings_path.exists()
    assert are_hooks_installed() is True


def test_install_registers_every_managed_event(settings_path):
    install_hooks()
    settings = _read(settings_path)
    for event in HOOKS_CONFIG:
        assert _our_command_count(settings, event) >= 1, f"{event} missing our hook"


# --- Preserving the user's own hooks (the core requirement) ---


def test_install_preserves_user_hook_on_managed_event(settings_path):
    """A user's own SessionStart hook must survive installation."""
    settings_path.write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "/my/own/script.sh"}]}
            ]
        }
    }))
    install_hooks()
    cmds = _commands_for(_read(settings_path), "SessionStart")
    assert "/my/own/script.sh" in cmds, "user's hook was clobbered"
    assert any(HOOK_COMMAND in c for c in cmds), "our hook was not added"


def test_install_preserves_unrelated_settings_keys(settings_path):
    settings_path.write_text(json.dumps({
        "model": "opus",
        "permissions": {"allow": ["Bash"]},
        "hooks": {},
    }))
    install_hooks()
    settings = _read(settings_path)
    assert settings["model"] == "opus"
    assert settings["permissions"] == {"allow": ["Bash"]}


def test_install_preserves_user_postooluse_with_different_matcher(settings_path):
    """Our AskUserQuestion PostToolUse hook must coexist with a user's Bash one."""
    settings_path.write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "/my/bash/hook"}]}
            ]
        }
    }))
    install_hooks()
    groups = _read(settings_path)["hooks"]["PostToolUse"]
    user_group = next((g for g in groups if g.get("matcher") == "Bash"), None)
    our_group = next((g for g in groups if g.get("matcher") == "AskUserQuestion"), None)
    assert user_group is not None, "user's Bash PostToolUse group was removed"
    assert any(h["command"] == "/my/bash/hook" for h in user_group["hooks"])
    assert our_group is not None, "our AskUserQuestion PostToolUse group missing"
    assert any(HOOK_COMMAND in h["command"] for h in our_group["hooks"])


def test_install_preserves_user_command_sharing_a_group(settings_path):
    """If the user shares a group with us, re-install keeps theirs and ours once."""
    settings_path.write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {"hooks": [
                    {"type": "command", "command": "/their/hook"},
                    {"type": "command", "command": HOOK_COMMAND},
                ]}
            ]
        }
    }))
    install_hooks()
    settings = _read(settings_path)
    cmds = _commands_for(settings, "SessionStart")
    assert "/their/hook" in cmds
    assert _our_command_count(settings, "SessionStart") == 1, "duplicated our hook"


# --- Idempotency ---


def test_install_is_idempotent(settings_path):
    install_hooks()
    install_hooks()
    install_hooks()
    settings = _read(settings_path)
    for event, entries in HOOKS_CONFIG.items():
        assert _our_command_count(settings, event) == len(entries), (
            f"{event} has duplicate Clawd Tank hooks after repeated installs"
        )


# --- Matcher-aware "outdated" detection ---


def test_are_hooks_installed_true_after_install(settings_path):
    install_hooks()
    assert are_hooks_installed() is True


def test_are_hooks_installed_false_when_event_missing(settings_path):
    install_hooks()
    settings = _read(settings_path)
    del settings["hooks"]["PostToolUse"]
    settings_path.write_text(json.dumps(settings))
    assert are_hooks_installed() is False


def test_are_hooks_installed_matcher_aware(settings_path):
    """Our command present under the WRONG matcher must count as not-installed."""
    install_hooks()
    settings = _read(settings_path)
    # Replace our AskUserQuestion group with a Bash-matched one (wrong matcher).
    settings["hooks"]["PostToolUse"] = [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": HOOK_COMMAND}]}
    ]
    settings_path.write_text(json.dumps(settings))
    assert are_hooks_installed() is False


def test_are_hooks_installed_false_on_empty_settings(settings_path):
    settings_path.write_text(json.dumps({}))
    assert are_hooks_installed() is False
